"""Encode and decode packets for the device protocol."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Any, TypeAlias
from device_definitions import DeviceInfo
from calibration_protocol import CalibrationInfo, CalibrationTable

from protocol_definitions import (
    SOF1_BIN,
    SOF2_BIN,
    ControlMode,
    MsgType,
    CommandErrorCode,
    ERROR_REPLY_TYPES,
)


_LOG = logging.getLogger(__name__)

# Wire-format layouts. Keeping these in one place makes the protocol easier to audit.
_FRAME_HEADER = struct.Struct("<BBBH")  # SOF1, SOF2, message type, payload length
_LOG_HEADER = struct.Struct("<IHH")     # timestamp, sample count, signal count
_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")
_PID_GAINS = struct.Struct("<fff")


@dataclass
class LogPayload:
    timestamp: int
    sample_count: int
    signal_count: int
    enabled_signals: list[dict[str, Any]]
    signals: dict[str, list[int | float]]

@dataclass
class PIDPayload:
    controller_id: int
    kp: float | None
    ki: float | None
    kd: float | None

@dataclass
class VarPayload:
    var_id: int
    value: int | float | None

@dataclass
class TextPayload:
    text: str

@dataclass(frozen=True)
class CommandErrorPayload:
    """One complete error: request command, numeric reason, and driver text."""

    command: int
    code: int
    message: str

    @classmethod
    def decode(cls, payload: bytes) -> CommandErrorPayload:
        if not 4 <= len(payload) <= 163:
            raise ValueError("Invalid command-error length")
        command, code = struct.unpack_from("<BH", payload)
        message = payload[3:].decode("utf-8")
        if code == 0 or not message.strip() or "\0" in message:
            raise ValueError("Invalid command-error code or message")
        return cls(command, code, message)

    def encode(self) -> bytes:
        payload = struct.pack("<BH", self.command, self.code) + self.message.encode("utf-8")
        self.decode(payload)
        return payload

    def __str__(self) -> str:
        try:
            command = MsgType(self.command).name
        except ValueError:
            command = f"command 0x{self.command:02X}"
        try:
            reason = CommandErrorCode(self.code).name
        except ValueError:
            reason = "UNKNOWN_ERROR"
        return f"{command} rejected [{reason}, 0x{self.code:04X}]: {self.message}"

@dataclass
class StatePayload:
    state: int

@dataclass
class MaskPayload:
    mask: bytes

@dataclass
class NodeIdPayload:
    node_id: int

@dataclass
class CanCyclicTypePayload:
    cyclic_type: int

@dataclass
class CanCyclicRatePayload:
    cyclic_type: int
    rate_cycles: int

@dataclass
class ErrorFlagsPayload:
    value: int

@dataclass
class ControlModePayload:
    mode: ControlMode

@dataclass
class RawPacket:
    msg_type_int: int
    payload_bytes: bytes

@dataclass(frozen=True)
class EncoderSourceInfo:
    source_id: int
    name: str
    calibratable: bool

@dataclass(frozen=True)
class EncoderInfoPayload:
    selected: int
    flags: int
    stage: int
    error: int
    progress: int
    sources: tuple[EncoderSourceInfo, ...]

    @classmethod
    def decode(cls, payload: bytes) -> EncoderInfoPayload:
        if len(payload) < 7:
            raise ValueError("Truncated encoder info")
        selected, flags, stage, error, progress, count = struct.unpack_from("<BBBBHB", payload)
        if flags & ~15 or stage > 8 or error > 7 or progress > 1000 or not 1 <= count <= 8:
            raise ValueError("Invalid encoder status")
        sources = []
        offset = 7
        for _ in range(count):
            if offset + 3 > len(payload):
                raise ValueError("Truncated encoder source")
            identifier, capabilities, size = payload[offset:offset + 3]
            offset += 3
            if capabilities & ~1 or not 1 <= size <= 31 or offset + size > len(payload):
                raise ValueError("Invalid encoder source")
            name = payload[offset:offset + size].decode("ascii")
            if not name.strip() or any(ord(char) < 32 or ord(char) > 126 for char in name):
                raise ValueError("Invalid encoder source name")
            sources.append(EncoderSourceInfo(identifier, name, bool(capabilities & 1)))
            offset += size
        if offset != len(payload) or len({s.source_id for s in sources}) != count or len({s.name for s in sources}) != count:
            raise ValueError("Duplicate encoder source or trailing data")
        return cls(selected, flags, stage, error, progress, tuple(sources))

    def encode(self) -> bytes:
        payload = struct.pack("<BBBBHB", self.selected, self.flags, self.stage, self.error, self.progress, len(self.sources))
        for source in self.sources:
            name = source.name.encode("ascii")
            payload += bytes((source.source_id, int(source.calibratable), len(name))) + name
        self.decode(payload)
        return payload

@dataclass(frozen=True)
class EncoderSourcePayload:
    source_id: int

@dataclass(frozen=True)
class EncoderCorrectionPayload:
    enabled: bool

DecodedPayload: TypeAlias = (
    LogPayload | PIDPayload | TextPayload | VarPayload | DeviceInfo
    | StatePayload | MaskPayload | NodeIdPayload | CanCyclicTypePayload
    | CanCyclicRatePayload | ErrorFlagsPayload | ControlModePayload | CommandErrorPayload
    | EncoderInfoPayload | EncoderSourcePayload | EncoderCorrectionPayload
    | CalibrationInfo | CalibrationTable
)
PacketData: TypeAlias = DecodedPayload | bytes | None

@dataclass
class Packet:
    msg_type: MsgType
    data: PacketData



def mask_bytes_to_int(mask: bytes | bytearray) -> int:
    """Convert little-endian mask bytes to an integer."""
    return int.from_bytes(mask, byteorder="little")


def mask_int_to_bytes(mask: int, length: int) -> bytes:
    """Convert an integer to a fixed-length little-endian mask."""
    if mask < 0:
        raise ValueError("mask must be non-negative")
    try:
        return mask.to_bytes(length, byteorder="little")
    except OverflowError as exc:
        raise ValueError(f"mask does not fit in {length} bytes") from exc


class ProtocolCodec:
    """Packet framing plus message-specific payload encoding and decoding."""

    def __init__(
        self,
        log_mask: bytes | bytearray | None = None,
        sof1_bin: int = SOF1_BIN,
        sof2_bin: int = SOF2_BIN,
        signal_list: list[dict[str, Any]] | None = None,
    ) -> None:
        self.signal_list = list(signal_list or [])
        self.variable_list = []
        self.pid_list = []
        self.mask_bytes = (len(self.signal_list) + 7) // 8
        self.max_signals = len(self.signal_list)
        self.max_reply = 1019
        if log_mask is None:
            log_mask = bytes(self.mask_bytes)
        self._validate_mask(log_mask)

        for name, value in (("sof1_bin", sof1_bin), ("sof2_bin", sof2_bin)):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{name} must fit in one byte")

        self.log_mask = bytes(log_mask)
        self.sof1_bin = sof1_bin
        self.sof2_bin = sof2_bin
        self.running = False

    def set_definitions(self, definitions, info) -> None:
        definitions.validate(info)
        self.signal_list = [dict(entry) for entry in definitions.signals]
        self.variable_list = [dict(entry) for entry in definitions.variables]
        self.pid_list = [dict(entry) for entry in definitions.pids]
        self.mask_bytes = info.mask_bytes
        self.max_signals = info.max_signals
        self.max_reply = info.max_reply
        self.log_mask = bytes(self.mask_bytes)

    def clear_definitions(self) -> None:
        self.signal_list = []
        self.variable_list = []
        self.pid_list = []
        self.mask_bytes = 0
        self.max_signals = 0
        self.max_reply = 1019
        self.log_mask = b""

    def _validate_mask(self, log_mask: bytes | bytearray) -> None:
        if len(log_mask) != self.mask_bytes:
            raise ValueError(f"log_mask must be {self.mask_bytes} bytes")
        value = int.from_bytes(log_mask, "little")
        if value >> len(self.signal_list) or value.bit_count() > self.max_signals:
            raise ValueError("Signal mask exceeds discovered capabilities")

    @staticmethod
    def u32_to_f32(value: int) -> float:
        return _F32.unpack(_U32.pack(value))[0]

    @staticmethod
    def u32_to_i32(value: int) -> int:
        return _I32.unpack(_U32.pack(value))[0]

    @classmethod
    def cast_u32_value(cls, value: int, value_type: str) -> int | float:
        """Interpret the same four wire bytes as unsigned, signed, or float."""
        if value_type == "u32":
            return value
        if value_type == "i32":
            return cls.u32_to_i32(value)
        if value_type == "f":
            return cls.u32_to_f32(value)
        raise ValueError(f"Unsupported type: {value_type}")

    @staticmethod
    def f32_to_u32(value: float) -> int:
        return _U32.unpack(_F32.pack(float(value)))[0]

    @staticmethod
    def i32_to_u32(value: int) -> int:
        return _U32.unpack(_I32.pack(int(value)))[0]

    @classmethod
    def cast_to_u32_value(cls, value: int | float, value_type: str) -> int:
        """Encode a typed value as its four-byte unsigned wire representation."""
        if value_type == "u32":
            return int(value)
        if value_type == "i32":
            return cls.i32_to_u32(int(value))
        if value_type == "f":
            return cls.f32_to_u32(float(value))
        raise ValueError(f"Unsupported type: {value_type}")

    def set_log_mask(self, log_mask: bytes | bytearray) -> None:
        self._validate_mask(log_mask)
        self.log_mask = bytes(log_mask)

    def get_enabled_signals(self, log_mask: bytes | bytearray | None = None) -> list[dict[str, Any]]:
        mask = self.log_mask if log_mask is None else log_mask
        self._validate_mask(mask)

        enabled: list[dict[str, Any]] = []
        for signal in self.signal_list:
            bit = int(signal["bit"])
            if bit < 0:
                raise ValueError(f"Signal bit must be non-negative: {bit}")
            byte_index, bit_index = divmod(bit, 8)
            if byte_index < len(mask) and mask[byte_index] & (1 << bit_index):
                enabled.append(signal)
        return enabled

    def extract_packets(self, buffer: bytearray) -> list[RawPacket]:
        """Extract complete frames, leaving any incomplete frame in ``buffer``."""
        packets: list[RawPacket] = []
        header_size = _FRAME_HEADER.size
        sof = bytes((self.sof1_bin, self.sof2_bin))

        while True:
            start = buffer.find(sof)
            if start < 0:
                keep_last_byte = bool(buffer) and buffer[-1] == self.sof1_bin
                if keep_last_byte:
                    del buffer[:-1]
                else:
                    buffer.clear()
                break

            if start:
                del buffer[:start]

            if len(buffer) < header_size:
                break

            msg_type_int = buffer[2]
            payload_length = int.from_bytes(buffer[3:5], byteorder="little")
            packet_length = header_size + payload_length
            if payload_length > self.max_reply:
                del buffer[0]
                continue
            if len(buffer) < packet_length:
                break

            packets.append(
                RawPacket(
                    msg_type_int=msg_type_int,
                    payload_bytes=bytes(buffer[header_size:packet_length]),
                )
            )
            del buffer[:packet_length]

        return packets

    def build_packet(self, raw_packet: RawPacket) -> bytes:
        msg_type_int = int(raw_packet.msg_type_int)
        if not 0 <= msg_type_int <= 0xFF:
            raise ValueError("msg_type must fit in one byte")
        if len(raw_packet.payload_bytes) > 0xFFFF:
            raise ValueError("payload too large for 16-bit length")

        return _FRAME_HEADER.pack(self.sof1_bin, self.sof2_bin, msg_type_int, len(raw_packet.payload_bytes)) + raw_packet.payload_bytes

    def decode_packet(self, raw_packet: RawPacket) -> Packet | None:
        try:
            msg_type_int = MsgType(raw_packet.msg_type_int)
        except ValueError:
            _LOG.warning("Unknown packet type: %s", raw_packet.msg_type_int)
            return None

        if msg_type_int in ERROR_REPLY_TYPES:
            try:
                return Packet(msg_type_int, CommandErrorPayload.decode(raw_packet.payload_bytes))
            except (ValueError, UnicodeError):
                return Packet(msg_type_int, None)

        if msg_type_int in (MsgType.MSG_CALIBRATION_INFO_REPLY, MsgType.MSG_CALIBRATION_TABLE_REPLY):
            try:
                value = (CalibrationInfo.decode(raw_packet.payload_bytes) if msg_type_int == MsgType.MSG_CALIBRATION_INFO_REPLY
                         else CalibrationTable.decode(raw_packet.payload_bytes))
                return Packet(msg_type_int, value)
            except ValueError:
                return Packet(msg_type_int, None)

        if msg_type_int == MsgType.MSG_ENCODER_INFO_REPLY:
            try:
                return Packet(msg_type_int, EncoderInfoPayload.decode(raw_packet.payload_bytes))
            except (ValueError, UnicodeError):
                return Packet(msg_type_int, None)

        decoder = {
            MsgType.MSG_LOG_DATA: self._decode_log_payload,
            MsgType.MSG_PID_REPLY: self._decode_pid_payload,
            MsgType.MSG_TEXT_REPLY: self._decode_text_payload,
            MsgType.MSG_VAR_REPLY: self._decode_var_payload,
            MsgType.MSG_DEVICE_INFO_REPLY: self._decode_device_info_payload,
            MsgType.MSG_STATE_REPLY: self._decode_state_payload,
            MsgType.MSG_MASK_REPLY: self._decode_mask_payload,
            MsgType.MSG_NODE_ID_REPLY: self._decode_node_id_payload,
            MsgType.MSG_ACTIVE_ERRORS_REPLY: self._decode_error_flags_payload,
            MsgType.MSG_LATCHED_ERRORS_REPLY: self._decode_error_flags_payload,
            MsgType.MSG_CAN_CYCLIC_REPLY: self._decode_can_cyclic_rate_payload,
            MsgType.MSG_CONTROL_MODE_REPLY: self._decode_control_mode_payload,
        }.get(msg_type_int)

        if decoder is None:
            # Preserve known-but-not-yet-decoded messages (ACK, device errors,
            # etc.) so callers can inspect or log their bytes.
            _LOG.debug("No payload decoder for message type %s", msg_type_int.name)
            return Packet(msg_type=msg_type_int, data=raw_packet.payload_bytes)

        decoded: DecodedPayload | None = decoder(raw_packet.payload_bytes)
        if decoded is None:
            return Packet(msg_type=msg_type_int, data=None)
        if isinstance(decoded, MaskPayload):
            self.set_log_mask(decoded.mask)
        return Packet(msg_type=msg_type_int, data=decoded)

    def encode_packet(self, packet: Packet) -> RawPacket:
        """Encode a typed packet; reject mismatched payload types explicitly."""
        msg_type = packet.msg_type
        data = packet.data

        if data is None:
            empty_payload_types = {
                MsgType.MSG_GET_DEVICE_INFO,
                MsgType.MSG_ENTER_BOOTLOADER,
                MsgType.MSG_GET_MASK,
                MsgType.MSG_START_LOG,
                MsgType.MSG_STOP_LOG,
                MsgType.MSG_FLASH_SAVE,
                MsgType.MSG_FLASH_LOAD,
                MsgType.MSG_FLASH_CLEAR,
                MsgType.MSG_GET_STATE,
                MsgType.MSG_GET_NODE_ID,
                MsgType.MSG_GET_ACTIVE_ERRORS,
                MsgType.MSG_GET_LATCHED_ERRORS,
                MsgType.MSG_CLEAR_LATCHED_ERRORS,
                MsgType.MSG_GET_CONTROL_MODE,
                MsgType.MSG_GET_ENCODER_INFO,
            }
            if msg_type not in empty_payload_types:
                raise ValueError(f"{msg_type.name} requires a payload")
            return RawPacket(msg_type_int=int(msg_type), payload_bytes=b"")

        if msg_type == MsgType.MSG_SET_ENCODER_SOURCE and isinstance(data, EncoderSourcePayload):
            if not 0 <= data.source_id <= 255:
                raise ValueError("Encoder source ID must fit in one byte")
            payload = bytes((data.source_id,))
        elif msg_type == MsgType.MSG_SET_ENCODER_CORRECTION and isinstance(data, EncoderCorrectionPayload):
            payload = bytes((int(data.enabled),))
        elif msg_type == MsgType.MSG_GET_PID and isinstance(data, PIDPayload):
            payload = struct.pack("<B", data.controller_id)
        elif msg_type == MsgType.MSG_SET_PID and isinstance(data, PIDPayload):
            gains = (data.kp, data.ki, data.kd)
            if any(gain is None for gain in gains):
                raise ValueError("PID gains must all be set for MSG_SET_PID")
            payload = struct.pack(
                "<Bfff", data.controller_id, data.kp, data.ki, data.kd
            )
        elif msg_type == MsgType.MSG_TEXT_COMMAND and isinstance(data, TextPayload):
            payload = self._encode_text_payload(data)
        elif msg_type == MsgType.MSG_SET_CONTROL_MODE and isinstance(data, ControlModePayload):
            try:
                mode = ControlMode(int(data.mode))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid control mode: {data.mode!r}") from exc
            payload = struct.pack("<B", int(mode))
        elif msg_type == MsgType.MSG_SET_STATE and isinstance(data, StatePayload):
            if not 0 <= int(data.state) <= 0xFF:
                raise ValueError("FOC state must fit in one byte")
            payload = struct.pack("<B", int(data.state))
        elif msg_type == MsgType.MSG_SET_MASK and isinstance(data, (bytes, bytearray)):
            self._validate_mask(data)
            payload = bytes(data)
        elif msg_type == MsgType.MSG_SET_NODE_ID and isinstance(data, NodeIdPayload):
            if not 0 <= int(data.node_id) <= 15:
                raise ValueError("CAN node ID must be between 0 and 15")
            payload = struct.pack("<B", int(data.node_id))
        elif msg_type == MsgType.MSG_SET_CAN_CYCLIC_RATE and isinstance(data, CanCyclicRatePayload):
            self._validate_can_cyclic_type(data.cyclic_type)
            if not 0 <= int(data.rate_cycles) <= 0xFFFFFFFF:
                raise ValueError("CAN cyclic rate must fit in an unsigned 32-bit value")
            payload = struct.pack("<BI", int(data.cyclic_type), int(data.rate_cycles))
        elif msg_type == MsgType.MSG_GET_CAN_CYCLIC_RATE and isinstance(data, CanCyclicTypePayload):
            self._validate_can_cyclic_type(data.cyclic_type)
            payload = struct.pack("<B", int(data.cyclic_type))
        elif msg_type == MsgType.MSG_GET_VAR and isinstance(data, VarPayload):
            payload = struct.pack("<B", data.var_id)
        elif msg_type == MsgType.MSG_SET_VAR and isinstance(data, VarPayload):
            payload = self._encode_var_payload(data)
        elif isinstance(data, bytes):
            # Raw payload escape hatch for message types whose wire layout is
            # not modeled here (e.g. error messages).
            payload = data
        else:
            raise TypeError(
                f"Unsupported data type {type(data).__name__} for {msg_type!r}"
            )

        return RawPacket(msg_type_int=int(msg_type), payload_bytes=payload)

    @staticmethod
    def _decode_device_info_payload(payload: bytes) -> DeviceInfo | None:
        try:
            return DeviceInfo.decode(payload)
        except ValueError as error:
            _LOG.warning("Invalid DEVICE_INFO_REPLY: %s", error)
            return None

    @staticmethod
    def _decode_state_payload(payload: bytes) -> StatePayload | None:
        if len(payload) != 1:
            _LOG.warning("Invalid STATE_REPLY payload length: %d, expected 1", len(payload))
            return None
        return StatePayload(state=payload[0])

    def _decode_mask_payload(self, payload: bytes) -> MaskPayload | None:
        if len(payload) != self.mask_bytes:
            _LOG.warning(
                "Invalid MASK_REPLY payload length: %d, expected %d",
                len(payload),
                self.mask_bytes,
            )
            return None
        return MaskPayload(mask=bytes(payload))

    @staticmethod
    def _decode_node_id_payload(payload: bytes) -> NodeIdPayload | None:
        if len(payload) != 1:
            _LOG.warning("Invalid NODE_ID_REPLY payload length: %d, expected 1", len(payload))
            return None
        return NodeIdPayload(node_id=payload[0])

    @staticmethod
    def _decode_error_flags_payload(payload: bytes) -> ErrorFlagsPayload | None:
        if len(payload) != 4:
            _LOG.warning("Invalid error-flags payload length: %d, expected 4", len(payload))
            return None
        return ErrorFlagsPayload(value=struct.unpack("<I", payload)[0])

    @staticmethod
    def _decode_control_mode_payload(payload: bytes) -> ControlModePayload | None:
        if len(payload) != 1:
            _LOG.warning(
                "Invalid CONTROL_MODE_REPLY payload length: %d, expected 1",
                len(payload),
            )
            return None
        try:
            mode = ControlMode(payload[0])
        except ValueError:
            _LOG.warning("Unknown control mode in reply: %d", payload[0])
            return None
        return ControlModePayload(mode=mode)

    @staticmethod
    def _validate_can_cyclic_type(cyclic_type: int) -> None:
        if not 0 <= int(cyclic_type) <= 0xFF:
            raise ValueError("CAN cyclic type must fit in one byte")

    @staticmethod
    def _decode_can_cyclic_rate_payload(payload: bytes) -> CanCyclicRatePayload | None:
        if len(payload) != 5:
            _LOG.warning(
                "Invalid CAN_CYCLIC_REPLY payload length: %d, expected 5",
                len(payload),
            )
            return None
        cyclic_type, rate_cycles = struct.unpack("<BI", payload)
        return CanCyclicRatePayload(
            cyclic_type=cyclic_type,
            rate_cycles=rate_cycles,
        )

    def _decode_log_payload(self, payload: bytes, log_mask: bytes | bytearray | None = None) -> LogPayload | None:
        if len(payload) < _LOG_HEADER.size:
            _LOG.warning("Log payload is shorter than its header")
            return None

        timestamp, sample_count, signal_count = _LOG_HEADER.unpack_from(payload)
        enabled_signals = self.get_enabled_signals(log_mask)
        if len(enabled_signals) != signal_count:
            _LOG.warning(
                "Mask enables %d signals, but payload says signal_count=%d",
                len(enabled_signals),
                signal_count,
            )
            return None

        value_count = sample_count * signal_count
        expected_size = _LOG_HEADER.size + value_count * _U32.size
        if len(payload) != expected_size:
            _LOG.warning(
                "Invalid log payload size: got %d bytes, expected %d",
                len(payload),
                expected_size,
            )
            return None

        raw_values = struct.unpack_from(f"<{value_count}I", payload, _LOG_HEADER.size)
        signal_buffers = {signal["name"]: [] for signal in enabled_signals}

        for sample_index in range(sample_count):
            row_start = sample_index * signal_count
            for signal_index, signal in enumerate(enabled_signals):
                raw_value = raw_values[row_start + signal_index]
                value = self.cast_u32_value(raw_value, signal["type"])
                signal_buffers[signal["name"]].append(value)

        return LogPayload(
            timestamp=timestamp,
            sample_count=sample_count,
            signal_count=signal_count,
            enabled_signals=enabled_signals,
            signals=signal_buffers,
        )

    def _decode_pid_payload(self, payload: bytes) -> PIDPayload | None:
        expected_size = 1 + _PID_GAINS.size
        if len(payload) != expected_size:
            _LOG.warning(
                "Invalid PID_REPLY payload length: got %d, expected %d",
                len(payload),
                expected_size,
            )
            return None

        controller_id = payload[0]
        kp, ki, kd = _PID_GAINS.unpack_from(payload, 1)
        return PIDPayload(controller_id=controller_id, kp=kp, ki=ki, kd=kd)

    def _decode_var_payload(self, payload: bytes) -> VarPayload | None:
        if len(payload) != 1 + _U32.size:
            _LOG.warning("Invalid VAR_REPLY payload length: %d", len(payload))
            return None

        var_id = payload[0]
        raw_value = _U32.unpack_from(payload, 1)[0]
        var_meta = next((item for item in self.variable_list if item["id"] == var_id), None)
        if var_meta is None:
            _LOG.warning("Unknown var_id: %d", var_id)
            return None

        try:
            value = self.cast_u32_value(raw_value, var_meta["type"])
        except ValueError:
            _LOG.exception("Unsupported type for var_id %d", var_id)
            return None

        return VarPayload(var_id=var_id, value=value)

    def _encode_var_payload(self, var_payload: VarPayload) -> bytes:
        var_meta = next(
            (item for item in self.variable_list if item["id"] == var_payload.var_id), None
        )
        if var_meta is None:
            raise ValueError(f"Unknown var_id: {var_payload.var_id}")
        if var_payload.value is None:
            raise ValueError(f"Var value is None for var_id: {var_payload.var_id}")

        raw_value = self.cast_to_u32_value(var_payload.value, var_meta["type"])
        return struct.pack("<BI", var_payload.var_id, raw_value)

    @staticmethod
    def _encode_text_payload(text_payload: TextPayload) -> bytes:
        return (text_payload.text + "\n").encode("utf-8")

    @staticmethod
    def _decode_text_payload(payload: bytes) -> TextPayload | None:
        try:
            return TextPayload(text=payload.decode("utf-8"))
        except UnicodeDecodeError:
            _LOG.warning("Failed to decode text payload as UTF-8")
            return None
