"""One outstanding command; discover or validate cached metadata before telemetry."""
from collections import deque
from dataclasses import replace
import struct
import threading
import time

from device_definitions import DeviceInfo, Definitions, DefinitionCache, SelectionStore, PAGE_HEADER, parse_rows
from protocol_codec import Packet, CommandErrorPayload, EncoderInfoPayload
from calibration_protocol import CalibrationInfo, CalibrationTable
from configuration_protocol import decode_configuration
from protocol_definitions import MsgType, ERROR_REPLY_TYPES

REPLIES = {
    MsgType.MSG_GET_CONFIGURATION: MsgType.MSG_CONFIGURATION_REPLY,
    MsgType.MSG_GET_TARGETS: MsgType.MSG_TARGETS_REPLY,
    MsgType.MSG_GET_CALIBRATION_INFO: MsgType.MSG_CALIBRATION_INFO_REPLY,
    MsgType.MSG_GET_CALIBRATION_TABLE: MsgType.MSG_CALIBRATION_TABLE_REPLY,
    MsgType.MSG_GET_DEVICE_INFO: MsgType.MSG_DEVICE_INFO_REPLY,
    MsgType.MSG_GET_DEFINITIONS: MsgType.MSG_DEFINITIONS_REPLY,
    MsgType.MSG_GET_MASK: MsgType.MSG_MASK_REPLY,
    MsgType.MSG_GET_PID: MsgType.MSG_PID_REPLY,
    MsgType.MSG_GET_VAR: MsgType.MSG_VAR_REPLY,
    MsgType.MSG_GET_STATE: MsgType.MSG_STATE_REPLY,
    MsgType.MSG_GET_NODE_ID: MsgType.MSG_NODE_ID_REPLY,
    MsgType.MSG_GET_ACTIVE_ERRORS: MsgType.MSG_ACTIVE_ERRORS_REPLY,
    MsgType.MSG_GET_LATCHED_ERRORS: MsgType.MSG_LATCHED_ERRORS_REPLY,
    MsgType.MSG_GET_CAN_CYCLIC_RATE: MsgType.MSG_CAN_CYCLIC_REPLY,
    MsgType.MSG_GET_CONTROL_MODE: MsgType.MSG_CONTROL_MODE_REPLY,
    MsgType.MSG_GET_ENCODER_INFO: MsgType.MSG_ENCODER_INFO_REPLY,
}
ERRORS = ERROR_REPLY_TYPES


class ProtocolSession:
    def __init__(self, codec, transmit, ready, failed, sample_rate: float = 8000, clock=time.monotonic,
                 logging_changed=None, definitions_ready=None, cache=None, selections=None,
                 rejected=None):
        self.codec, self.transmit = codec, transmit
        self.ready_callback, self.failed_callback = ready, failed
        self.rejected_callback = rejected or (lambda message: None)
        self.sample_rate, self.clock = sample_rate, clock
        self.logging_changed = logging_changed or (lambda active: None)
        self.definitions_ready = definitions_ready or (lambda definitions, info, mask: None)
        self.cache = cache if cache is not None else DefinitionCache()
        self.selections = selections if selections is not None else SelectionStore()
        self.lock = threading.RLock()
        self.commands = deque()
        self.completions = {}
        self.pending = None
        self.active = False
        self.initializing = False
        self.deadline = 0.0
        self.info = None
        self.definitions = None
        self.firmware_version = None
        self.requested_mask = None
        self.cache_hit = False
        self.phase = "disconnected"
        self.info_attempts = 0

    def start(self, selected_names=None):
        with self.lock:
            self.stop()
            self.selected_names = list(selected_names) if selected_names is not None else self.selections.load()
            self.active = self.initializing = True
            self.cache_hit = False
            self.phase = "info"
            self.info_attempts = 1
            self._enqueue(Packet(MsgType.MSG_GET_DEVICE_INFO, None))
            self._pump()

    def stop(self):
        with self.lock:
            self.active = self.initializing = False
            self.commands.clear()
            self.pending = None
            callbacks = list(self.completions.values())
            self.completions.clear()
            self.requested_mask = None
            self.info = self.definitions = self.firmware_version = None
            self.phase = "disconnected"
            self.codec.clear_definitions()
            for callback in callbacks:
                callback(False)

    def send(self, packet, completed=None):
        with self.lock:
            if not self.active or self.initializing:
                if completed is not None:
                    completed(False)
                return
            self._enqueue(packet, completed)
            self._pump()

    def _enqueue(self, packet, completed=None):
        raw = self.codec.encode_packet(packet)
        if self.info is not None and len(raw.payload_bytes) > self.info.max_command:
            raise ValueError("Command exceeds the device's advertised payload limit")
        self.commands.append(raw)
        if packet.msg_type == MsgType.MSG_SET_MASK:
            confirmation = self.codec.encode_packet(Packet(MsgType.MSG_GET_MASK, None))
            self.commands.append(confirmation)
        else:
            confirmation = raw
        if completed is not None:
            self.completions[id(confirmation)] = completed

    def _complete_pending(self, success):
        """Notify the request owner exactly once, including read-back completion."""
        callback = self.completions.pop(id(self.pending), None)
        self.pending = None
        if callback is not None:
            callback(success)

    def _cancel_queued(self):
        while self.commands:
            callback = self.completions.pop(id(self.commands.popleft()), None)
            if callback is not None:
                callback(False)

    def _pump(self):
        if self.active and self.pending is None and self.commands:
            self.pending = self.commands.popleft()
            self.deadline = self.clock() + 2.0
            self.transmit(self.codec.build_packet(self.pending))

    def tick(self):
        with self.lock:
            if self.active and self.pending is not None and self.clock() >= self.deadline:
                command = MsgType(self.pending.msg_type_int)
                if (self.initializing and self.phase == "info"
                        and command == MsgType.MSG_GET_DEVICE_INFO and self.info_attempts < 3):
                    # Only the read-only startup query is safe to repeat after
                    # a reboot. Writes and runtime commands are never retried.
                    self.info_attempts += 1
                    self.deadline = self.clock() + 2.0
                    self.transmit(self.codec.build_packet(self.pending))
                    return
                expected = REPLIES.get(command, MsgType.MSG_ACK)
                attempts = f", {self.info_attempts} startup attempts" if self.initializing and self.phase == "info" else ""
                self._fail(f"Timed out waiting for {expected.name} after {command.name} "
                           f"(phase: {self.phase}{attempts}). "
                           "The device may have restarted or disconnected; reconnect.")

    def _fail(self, message):
        self.stop()
        self.failed_callback(message)

    def _reject(self, command, message):
        """Finish a denied runtime command without discarding the connection."""
        self._complete_pending(False)
        if command in (MsgType.MSG_STOP_LOG, MsgType.MSG_SET_MASK):
            # Mask changes queue STOP -> SET_MASK -> GET_MASK -> START. A rejected
            # prerequisite cancels the remaining batch rather than resuming it.
            self._cancel_queued()
            self.requested_mask = None
        self.rejected_callback(message)
        self._pump()

    def _request_page(self, kind, start):
        self._enqueue(Packet(MsgType.MSG_GET_DEFINITIONS, struct.pack("<BH", kind, start)))

    def _begin_discovery(self):
        if self.info is None:
            raise ValueError("Discovery requested before device info")
        self.phase = "discovery"
        self.parts = [[] for _ in self.info.counts]
        cached = self.cache.load(self.info)
        if cached is not None:
            self.cache_hit = True
            self._definitions_complete(cached)
        else:
            self._request_page(0, 0)

    def _definitions_complete(self, definitions):
        if self.info is None:
            raise ValueError("Definitions received before device info")
        definitions.validate(self.info)
        self.definitions = definitions
        self.codec.set_definitions(definitions, self.info)
        mask = definitions.mask_for_names(self.selected_names, self.info.max_signals)
        self.definitions_ready(definitions, self.info, mask)
        self.phase = "configure"
        self._enqueue(Packet(MsgType.MSG_SET_MASK, mask))
        self._enqueue(Packet(MsgType.MSG_START_LOG, None))

    def _receive_page(self, raw):
        if self.pending is None or self.info is None:
            raise ValueError("Definition page received without a pending discovery request")
        if len(raw.payload_bytes) < PAGE_HEADER.size:
            raise ValueError("Truncated definition page")
        kind, start, total, returned = PAGE_HEADER.unpack_from(raw.payload_bytes)
        wanted_kind, wanted_start = struct.unpack("<BH", self.pending.payload_bytes)
        if kind >= len(self.info.counts) or (kind, start) != (wanted_kind, wanted_start) or total != self.info.counts[kind]:
            raise ValueError("Definition page does not match request/device info")
        rows = parse_rows(kind, raw.payload_bytes[PAGE_HEADER.size:].decode("utf-8"))
        if len(rows) != returned or start + returned > total or (returned == 0 and total != 0):
            raise ValueError("Incomplete or inconsistent definition page")
        key = "bit" if kind == 0 else "id"
        if any(row[key] != start + offset for offset, row in enumerate(rows)):
            raise ValueError("Duplicate or out-of-order definition IDs")
        self.parts[kind].extend(rows)
        if start + returned < total:
            self._request_page(kind, start + returned)
        elif kind + 1 < len(self.info.counts):
            self._request_page(kind + 1, 0)
        else:
            definitions = Definitions(*self.parts).validate(self.info)
            self.cache.save(self.info, definitions)
            self._definitions_complete(definitions)

    def receive(self, raw):
        with self.lock:
            if not self.active or self.pending is None:
                return
            command = MsgType(self.pending.msg_type_int)
            if raw.msg_type_int in ERRORS:
                try:
                    error = CommandErrorPayload.decode(raw.payload_bytes)
                    if error.command != command and not (
                        raw.msg_type_int == MsgType.MSG_BUFFER_OVERFLOW and error.command == 0xFF
                    ):
                        raise ValueError("Error reply does not match outstanding command")
                except (ValueError, UnicodeError) as exc:
                    self._fail(f"Malformed {MsgType(raw.msg_type_int).name}: {exc}")
                    return
                message = str(error)
                if self.initializing or raw.msg_type_int == MsgType.MSG_BUFFER_OVERFLOW:
                    self._fail(message)
                else:
                    self._reject(command, message)
                return
            expected = REPLIES.get(command, MsgType.MSG_ACK)
            if raw.msg_type_int != expected:
                return
            try:
                lengths = {MsgType.MSG_ACK: 0,
                           MsgType.MSG_MASK_REPLY: self.codec.mask_bytes, MsgType.MSG_PID_REPLY: 13,
                           MsgType.MSG_VAR_REPLY: 5, MsgType.MSG_STATE_REPLY: 1,
                           MsgType.MSG_NODE_ID_REPLY: 1, MsgType.MSG_ACTIVE_ERRORS_REPLY: 4,
                           MsgType.MSG_LATCHED_ERRORS_REPLY: 4, MsgType.MSG_CAN_CYCLIC_REPLY: 5,
                           MsgType.MSG_CONTROL_MODE_REPLY: 1}
                if expected == MsgType.MSG_CONFIGURATION_REPLY:
                    group, _values = decode_configuration(self.codec.configuration_list, raw.payload_bytes)
                    if bytes((group,)) != self.pending.payload_bytes:
                        raise ValueError("Configuration reply group mismatch")
                if expected == MsgType.MSG_TARGETS_REPLY and len(raw.payload_bytes) != 16:
                    raise ValueError("Malformed targets reply")
                if expected == MsgType.MSG_ENCODER_INFO_REPLY:
                    EncoderInfoPayload.decode(raw.payload_bytes)
                if expected == MsgType.MSG_CALIBRATION_INFO_REPLY:
                    info_cal = CalibrationInfo.decode(raw.payload_bytes)
                    if bytes((info_cal.kind,)) != self.pending.payload_bytes:
                        raise ValueError("Calibration info kind mismatch")
                if expected == MsgType.MSG_CALIBRATION_TABLE_REPLY:
                    table = CalibrationTable.decode(raw.payload_bytes)
                    if struct.pack("<BHB", table.kind, table.offset, len(table.values)) != self.pending.payload_bytes:
                        raise ValueError("Calibration table range mismatch")
                if expected in lengths and len(raw.payload_bytes) != lengths[expected]:
                    raise ValueError(f"Malformed {expected.name}")
                if command in (MsgType.MSG_GET_PID, MsgType.MSG_GET_VAR, MsgType.MSG_GET_CAN_CYCLIC_RATE):
                    if raw.payload_bytes[0] != self.pending.payload_bytes[0]:
                        raise ValueError("Reply ID does not match outstanding command")
                if command == MsgType.MSG_GET_DEVICE_INFO:
                    info = DeviceInfo.decode(raw.payload_bytes)
                    if not self.initializing and (
                            self.info is None or replace(self.info, firmware=info.firmware) != info):
                        raise ValueError("Device capabilities or definitions changed; reconnect to refresh discovery")
                    self.info = info
                    self.sample_rate = self.info.sample_rate
                    self.firmware_version = self.info.firmware
                    self.codec.max_reply = self.info.max_reply
                    if self.initializing:
                        self.phase = "stop"
                        self._enqueue(Packet(MsgType.MSG_STOP_LOG, None))
                elif command == MsgType.MSG_STOP_LOG and self.phase == "stop":
                    self._begin_discovery()
                elif command == MsgType.MSG_GET_DEFINITIONS:
                    self._receive_page(raw)
                elif command == MsgType.MSG_GET_MASK:
                    self.codec._validate_mask(raw.payload_bytes)
                    if self.requested_mask is not None and raw.payload_bytes != self.requested_mask:
                        raise ValueError("Device did not apply requested signal mask")
                    self.codec.set_log_mask(raw.payload_bytes)
                    self.requested_mask = None
                    self.selections.save([entry["name"] for entry in self.codec.get_enabled_signals()])
                elif command == MsgType.MSG_SET_MASK:
                    self.requested_mask = self.pending.payload_bytes
                if command in (MsgType.MSG_START_LOG, MsgType.MSG_STOP_LOG):
                    self.logging_changed(command == MsgType.MSG_START_LOG)
                self._complete_pending(True)
                if self.initializing and command == MsgType.MSG_START_LOG:
                    self.initializing = False
                    self.phase = "ready"
                    self.ready_callback()
                self._pump()
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                self._fail(str(error))
