"""Threaded, protocol-compatible simulated motor driver."""

from __future__ import annotations

import math
import queue
import re
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from config import (
    COMMAND_RATE_LIMIT_SECONDS,
    SIMULATOR_SAMPLES_PER_PACKET,
    SAMPLE_RATE,
)
from USB_debugger.protocol_codec import ProtocolCodec, RawPacket
from USB_debugger.protocol_definitions import (
    FOC_USB_DEBUG_SIGNAL_LIST,
    SIGNAL_MASK_BYTES,
    VAR_ID_LIST,
    CONTROL_MODE_LABELS,
    ControlMode,
    MsgType,
    FOCState,
)


@dataclass(frozen=True)
class SimulatorConfig:
    samples_per_packet: int = SIMULATOR_SAMPLES_PER_PACKET
    sample_rate: float = SAMPLE_RATE


class SimulatorWorker:
    """Drop-in worker for the debugger that never opens a serial port.

    It consumes the same framed command bytes as ``SerialWorker`` and sends
    framed replies/log samples through the same ``data_callback`` interface.
    """

    is_simulator = True

    _DEFAULT_PIDS = {
        0: (0.50, 18.0, 0.001),
        1: (0.45, 16.0, 0.001),
        2: (0.08, 0.015, 0.0),
        3: (1.20, 0.10, 0.0),
    }

    _DEFAULT_VARIABLES = {
        0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0,
        4: 20.0, 5: 48.0, 6: 58.0, 7: 20.0,
        8: 30.0, 9: 100.0, 10: 90.0, 11: 7,
        12: 0.05, 13: 0.00012, 14: 0.1, 15: 0,
        16: 0.5, 17: 100.0, 18: 1000.0,
    }

    def __init__(
        self,
        command_queue: queue.Queue[bytes],
        data_callback: Optional[Callable[[bytearray], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
        disconnect_callback: Optional[Callable[[], None]] = None,
        config: SimulatorConfig | None = None,
        ready_callback: Optional[Callable[[], None]] = None,
    ):
        self.command_queue = command_queue
        self.data_callback = data_callback
        self.error_callback = error_callback
        self.disconnect_callback = disconnect_callback
        self.ready_callback = ready_callback
        self.config = config or SimulatorConfig()
        max_samples_per_packet = (0xFFFF - struct.calcsize("<IHH")) // (
            4 * len(FOC_USB_DEBUG_SIGNAL_LIST)
        )
        if not 0 < self.config.samples_per_packet <= max_samples_per_packet:
            raise ValueError(
                f"samples_per_packet must be between 1 and {max_samples_per_packet}"
            )
        if self.config.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")

        self.codec = ProtocolCodec()
        self.rx_buffer = bytearray()
        self._command_buffer = bytearray()
        self._stop_event = threading.Event()
        self._logging = False
        self._state = int(FOCState.FOC_STATE_IDLE)
        self._control_mode: ControlMode | None = None
        self._timestamp = 0
        self._started_at = time.monotonic()
        self._pids = dict(self._DEFAULT_PIDS)
        self._variables = dict(self._DEFAULT_VARIABLES)
        self._saved_variables = dict(self._variables)
        self._can_node_id = 0
        self._can_heartbeat_rate_ms = 0
        self._active_errors = 0
        self._latched_errors = 0
        self._command_rate_limit_seconds = max(
            0.0, float(COMMAND_RATE_LIMIT_SECONDS)
        )
        self._next_command_at = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True, name="SimulatorWorker")

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout=timeout)

    def stop(self) -> None:
        self._stop_event.set()

    def send_bytes(self, data: bytes) -> None:
        """Accept raw framed bytes, matching the serial worker's interface."""
        self._handle_command_bytes(data)

    def _clear_command_queue(self) -> None:
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        try:
            self._clear_command_queue()
            # Discard stale traffic before allowing the caller to queue the
            # initial signal mask and logging commands.
            if self.ready_callback is not None:
                self.ready_callback()
            packet_interval = (
                self.config.samples_per_packet / self.config.sample_rate
            )
            next_log_at = time.monotonic()
            was_logging = False
            while not self._stop_event.is_set():
                did_work = False
                if self._command_rate_limit_seconds <= 0:
                    # Preserve the original drain-all behavior when disabled.
                    while not self._stop_event.is_set():
                        try:
                            command = self.command_queue.get_nowait()
                        except queue.Empty:
                            break
                        did_work = True
                        self._handle_command_bytes(command)
                elif time.monotonic() >= self._next_command_at:
                    try:
                        command = self.command_queue.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        did_work = True
                        self._handle_command_bytes(command)
                        self._next_command_at = (
                            time.monotonic() + self._command_rate_limit_seconds
                        )

                now = time.monotonic()
                if self._logging and not was_logging:
                    # Begin with a packet immediately when logging starts.
                    next_log_at = now
                was_logging = self._logging

                if self._logging and now >= next_log_at:
                    self._emit_log_packet()
                    # Each packet contains N samples at SAMPLE_RATE, so packets
                    # are separated by N / SAMPLE_RATE seconds.
                    next_log_at += packet_interval
                    did_work = True

                if not did_work:
                    wait_time = 0.01
                    if self._logging:
                        wait_time = min(wait_time, max(0.0, next_log_at - now))
                    self._stop_event.wait(wait_time)
        except Exception as exc:
            if self.error_callback:
                self.error_callback(exc)
            if self.disconnect_callback:
                self.disconnect_callback()

    def _handle_command_bytes(self, data: bytes | bytearray) -> None:
        self._command_buffer.extend(data)
        for raw_packet in self.codec.extract_packets(self._command_buffer):
            self._handle_packet(raw_packet)

    def _handle_packet(self, raw_packet: RawPacket) -> None:
        try:
            msg_type = MsgType(raw_packet.msg_type_int)
        except ValueError:
            self._reply(MsgType.MSG_UNKNOWN_TYPE, b"")
            return

        payload = raw_packet.payload_bytes
        if msg_type == MsgType.MSG_GET_VERSION:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(MsgType.MSG_VERSION_REPLY, bytes((1, 0, 0)))
        elif msg_type == MsgType.MSG_GET_MASK:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(MsgType.MSG_MASK_REPLY, self.codec.log_mask)
        elif msg_type == MsgType.MSG_SET_MASK:
            if len(payload) != SIGNAL_MASK_BYTES:
                self._invalid_payload(msg_type)
                return
            if self._logging:
                self._reply(MsgType.MSG_ERROR, b"")
                return
            self.codec.set_log_mask(payload)
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_START_LOG:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._logging = True
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_STOP_LOG:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._logging = False
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_PID:
            if len(payload) != 1:
                self._invalid_payload(msg_type)
                return
            controller_id = payload[0]
            gains = self._pids.get(controller_id)
            if gains is None:
                self._reply(MsgType.MSG_UNKNOWN_ID, b"")
            else:
                self._reply(MsgType.MSG_PID_REPLY, struct.pack("<Bfff", controller_id, *gains))
        elif msg_type == MsgType.MSG_SET_PID:
            if len(payload) != struct.calcsize("<Bfff"):
                self._invalid_payload(msg_type)
                return
            controller_id, kp, ki, kd = struct.unpack("<Bfff", payload)
            if controller_id not in self._pids:
                self._reply(MsgType.MSG_UNKNOWN_ID, b"")
            else:
                self._pids[controller_id] = (kp, ki, kd)
                self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_VAR:
            if len(payload) != 1:
                self._invalid_payload(msg_type)
                return
            self._reply_var(payload[0])
        elif msg_type == MsgType.MSG_SET_VAR:
            if len(payload) != struct.calcsize("<BI"):
                self._invalid_payload(msg_type)
                return
            var_id, raw_value = struct.unpack("<BI", payload)
            meta = self._var_meta(var_id)
            if meta is None:
                self._reply(MsgType.MSG_UNKNOWN_ID, b"")
                return
            self._variables[var_id] = ProtocolCodec.cast_u32_value(raw_value, meta["type"])
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_TEXT_COMMAND:
            try:
                text_command = payload.decode("utf-8").strip()
            except UnicodeDecodeError:
                self._invalid_payload(msg_type)
                return
            self._reply(
                MsgType.MSG_TEXT_REPLY,
                self._text_response(text_command).encode("utf-8"),
            )
        elif msg_type == MsgType.MSG_SET_STATE:
            if len(payload) != 1:
                self._invalid_payload(msg_type)
                return
            try:
                state = FOCState(payload[0])
                if state == FOCState.FOC_STATE_COUNT:
                    raise ValueError("FOC_STATE_COUNT is not a runtime state")
                previous_state = self._state
                self._state = int(state)
                if (
                    previous_state == int(FOCState.FOC_STATE_RUN)
                    and self._state != int(FOCState.FOC_STATE_RUN)
                ):
                    for var_id in (0, 1, 2, 3):
                        self._variables[var_id] = 0.0
                    self._control_mode = None
                elif (
                    self._state == int(FOCState.FOC_STATE_RUN)
                    and self._control_mode is None
                ):
                    self._control_mode = ControlMode.CONTROL_MODE_OPENLOOP
            except ValueError:
                self._reply(MsgType.MSG_UNKNOWN_ID, b"")
                return
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_STATE:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(MsgType.MSG_STATE_REPLY, bytes((self._state,)))
        elif msg_type == MsgType.MSG_SET_CONTROL_MODE:
            if len(payload) != 1:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_RUN):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            try:
                self._control_mode = ControlMode(payload[0])
            except ValueError:
                self._invalid_payload(msg_type)
                return
            # The firmware resets the setpoints when changing control modes.
            for var_id in (0, 1, 2, 3):
                self._variables[var_id] = 0.0
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_CONTROL_MODE:
            if payload:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_RUN):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            if self._control_mode is None:
                self._control_mode = ControlMode.CONTROL_MODE_OPENLOOP
            self._reply(
                MsgType.MSG_CONTROL_MODE_REPLY,
                bytes((int(self._control_mode),)),
            )
        elif msg_type == MsgType.MSG_SET_NODE_ID:
            if len(payload) != 1 or payload[0] > 15:
                self._invalid_payload(msg_type)
                return
            self._can_node_id = payload[0]
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_NODE_ID:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(MsgType.MSG_NODE_ID_REPLY, bytes((self._can_node_id,)))
        elif msg_type == MsgType.MSG_GET_ACTIVE_ERRORS:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(
                MsgType.MSG_ACTIVE_ERRORS_REPLY,
                struct.pack("<I", self._active_errors),
            )
        elif msg_type == MsgType.MSG_GET_LATCHED_ERRORS:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(
                MsgType.MSG_LATCHED_ERRORS_REPLY,
                struct.pack("<I", self._latched_errors),
            )
        elif msg_type == MsgType.MSG_CLEAR_LATCHED_ERRORS:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._latched_errors = 0
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_SET_CAN_HEARTBEAT:
            if len(payload) != 2:
                self._invalid_payload(msg_type)
                return
            self._can_heartbeat_rate_ms = struct.unpack("<H", payload)[0]
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_GET_CAN_HEARTBEAT:
            if payload:
                self._invalid_payload(msg_type)
                return
            self._reply(
                MsgType.MSG_CAN_HEARTBEAT_REPLY,
                struct.pack("<H", self._can_heartbeat_rate_ms),
            )
        elif msg_type == MsgType.MSG_FLASH_SAVE:
            if payload:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_IDLE):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            self._saved_variables = dict(self._variables)
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_FLASH_LOAD:
            if payload:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_IDLE):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            self._variables = dict(self._saved_variables)
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_FLASH_CLEAR:
            if payload:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_IDLE):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            # Clearing flash restores its contents to factory defaults. Keep
            # the current live values unchanged until a FLASH_LOAD is requested.
            self._saved_variables = dict(self._DEFAULT_VARIABLES)
            self._ack(msg_type)
        elif msg_type == MsgType.MSG_ENTER_BOOTLOADER:
            if payload:
                self._invalid_payload(msg_type)
                return
            if self._state != int(FOCState.FOC_STATE_IDLE):
                self._reply(MsgType.MSG_ERROR, b"")
                return
            self._logging = False
            self._state = int(FOCState.FOC_STATE_BOOTLOADER)
            self._ack(msg_type)
        else:
            # The simulator acknowledges commands it does not model, just as a
            # test target should, without pretending to perform device I/O.
            self._ack(msg_type)

    def _text_response(self, command: str) -> str:
        command = command.strip()
        lowered = command.lower()
        if lowered in ("help", "?"):
            return "Simulator commands: help, version, state, status; Mo/Ms/Mp; Sq/Ss/Sp<number>"
        if lowered == "version":
            return "Simulator firmware 1.0.0"
        if lowered in ("state", "status"):
            state_name = FOCState(self._state).name.removeprefix("FOC_STATE_")
            mode_name = (
                "none"
                if self._control_mode is None
                else CONTROL_MODE_LABELS[self._control_mode]
            )
            return f"Simulator state: {state_name}; control mode: {mode_name}"

        modes = {
            "mo": ControlMode.CONTROL_MODE_OPENLOOP,
            "mp": ControlMode.CONTROL_MODE_POSITION,
            "ms": ControlMode.CONTROL_MODE_SPEED,
        }
        if lowered in modes:
            self._control_mode = modes[lowered]
            self._state = int(FOCState.FOC_STATE_RUN)
            # Match the firmware behavior when switching control modes.
            for var_id in (0, 1, 2, 3):
                self._variables[var_id] = 0.0
            return f"Simulator control mode: {CONTROL_MODE_LABELS[self._control_mode]}"

        setpoint = re.fullmatch(r"(?i)(Sq|Sd|Ss|Sp)([-+]?(?:\d+(?:\.\d*)?|\.\d+))", command)
        if setpoint:
            variable_id = {"sd": 0, "sq": 1, "sp": 2, "ss": 3}[setpoint.group(1).lower()]
            value = float(setpoint.group(2))
            self._variables[variable_id] = value
            return f"Simulator {setpoint.group(1)} setpoint: {value:g}"

        return f"SIMULATOR: received: {command}"

    @staticmethod
    def _var_meta(var_id: int) -> dict | None:
        return next((item for item in VAR_ID_LIST if int(item["id"]) == var_id), None)

    def _reply_var(self, var_id: int) -> None:
        meta = self._var_meta(var_id)
        if meta is None:
            self._reply(MsgType.MSG_UNKNOWN_ID, b"")
            return
        raw_value = ProtocolCodec.cast_to_u32_value(self._variables[var_id], meta["type"])
        self._reply(MsgType.MSG_VAR_REPLY, struct.pack("<BI", var_id, raw_value))

    def _ack(self, command_type: MsgType) -> None:
        self._reply(MsgType.MSG_ACK, b"")

    def _invalid_payload(self, command_type: MsgType) -> None:
        self._reply(MsgType.MSG_INVALID_PAYLOAD, b"")

    def _reply(self, msg_type: MsgType, payload: bytes) -> None:
        self._emit_bytes(self.codec.build_packet(RawPacket(int(msg_type), payload)))

    def _emit_bytes(self, data: bytes) -> None:
        self.rx_buffer.extend(data)
        if self.data_callback:
            self.data_callback(self.rx_buffer)

    def _emit_log_packet(self) -> None:
        enabled_signals = self.codec.get_enabled_signals()
        sample_count = self.config.samples_per_packet
        payload = bytearray(struct.pack("<IHH", self._timestamp, sample_count, len(enabled_signals)))

        for _sample_index in range(sample_count):
            sample_timestamp = self._timestamp
            elapsed = sample_timestamp / self.config.sample_rate
            sample_values = self._sample_values(sample_timestamp, elapsed)
            for signal in enabled_signals:
                name = str(signal["name"])
                value = sample_values.get(name, 0.0)
                payload.extend(
                    struct.pack(
                        "<I",
                        ProtocolCodec.cast_to_u32_value(value, str(signal["type"])),
                    )
                )
            # Timestamp ticks correspond to individual samples at SAMPLE_RATE.
            self._timestamp = (self._timestamp + 1) & 0xFFFFFFFF

        self._emit_bytes(self.codec.build_packet(RawPacket(int(MsgType.MSG_LOG_DATA), bytes(payload))))

    def _sample_values(self, timestamp: int, elapsed: float) -> dict[str, int | float]:
        angle = 2.0 * math.pi * 0.8 * elapsed
        phase = angle + (timestamp / self.config.sample_rate) * 2.0 * math.pi * 3.0
        values: dict[str, int | float] = {
            "timestamp": timestamp,
            "adc_values.motor_temp": 35.0 + 0.4 * math.sin(elapsed / 8.0),
            "adc_values.mosfet_temp": 31.0 + 0.3 * math.sin(elapsed / 6.0),
            "adc_values.vbus": 48.0 + 0.5 * math.sin(elapsed / 2.0),
            "ibus": 2.0 + 0.8 * math.sin(phase),
            "adc_values.phase_current.a": 4.0 * math.sin(phase),
            "adc_values.phase_current.b": 4.0 * math.sin(phase - 2.0 * math.pi / 3.0),
            "adc_values.phase_current.c": 4.0 * math.sin(phase + 2.0 * math.pi / 3.0),
            "ab_current.alpha": 4.0 * math.sin(phase),
            "ab_current.beta": 4.0 * math.cos(phase),
            "dq_current.d": float(self._variables[0]) + 0.15 * math.sin(phase),
            "dq_current.q": float(self._variables[1]) + 0.15 * math.cos(phase),
            "dq_current_filtered.d": float(self._variables[0]) + 0.1 * math.sin(phase),
            "dq_current_filtered.q": float(self._variables[1]) + 0.1 * math.cos(phase),
            "phase_voltage.a": 24.0 * math.sin(phase),
            "phase_voltage.b": 24.0 * math.sin(phase - 2.0 * math.pi / 3.0),
            "phase_voltage.c": 24.0 * math.sin(phase + 2.0 * math.pi / 3.0),
            "ab_voltage.alpha": 24.0 * math.sin(phase),
            "ab_voltage.beta": 24.0 * math.cos(phase),
            "dq_voltage.d": 2.0 * math.sin(phase),
            "dq_voltage.q": 4.0 * math.cos(phase),
            "encoder_angle_mechanical_wrapped": angle % (2.0 * math.pi),
            "encoder_angle_mechanical_unwrapped": angle,
            "encoder_speed_mechanical": 0.8 + 0.05 * math.sin(elapsed),
            "encoder_angle_electrical": (angle * int(self._variables[11])) % (2.0 * math.pi),
            "encoder_speed_electrical": 0.8 * int(self._variables[11]) + 0.5 * math.sin(elapsed),
            "dq_current_setpoint.d": float(self._variables[0]),
            "dq_current_setpoint.q": float(self._variables[1]),
            "angle_setpoint": float(self._variables[2]),
            "speed_setpoint": float(self._variables[3]),
            "execution_time.loop_max": 120 + int(8 * abs(math.sin(phase))),
            "hfi.injection_phase": angle % (2.0 * math.pi),
            "hfi.i_alpha_l_raw": 0.2 * math.sin(phase * 2.0),
            "hfi.i_beta_l_raw": 0.2 * math.cos(phase * 2.0),
            "hfi.i_alpha_l_filtered": 0.15 * math.sin(phase * 2.0),
            "hfi.i_beta_l_filtered": 0.15 * math.cos(phase * 2.0),
        }
        return values
