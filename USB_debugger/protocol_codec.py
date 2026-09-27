"""Encode and decode packets for the device protocol."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Any, TypeAlias

from protocol_definitions import (
    FOC_USB_DEBUG_SIGNAL_LIST,
    SIGNAL_MASK_BYTES,
    SOF1_BIN,
    SOF2_BIN,
    VAR_ID_LIST,
    ControlMode,
    MsgType,
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

@dataclass
class VersionPayload:
    major: int
    minor: int
    patch: int

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
class CanHeartbeatPayload:
    rate_cycles: int

@dataclass
class CanEncoderRatePayload:
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

DecodedPayload: TypeAlias = (
    LogPayload | PIDPayload | TextPayload | VarPayload | VersionPayload
    | StatePayload | MaskPayload | NodeIdPayload | CanHeartbeatPayload
    | CanEncoderRatePayload | ErrorFlagsPayload | ControlModePayload
)
PacketData: TypeAlias = DecodedPayload | bytes | None

@dataclass
class Packet:
    msg_type: MsgType
    data: PacketData



def mask_bytes_to_int(mask: bytes | bytearray) -> int:
    """Convert little-endian mask bytes to an integer."""
    return int.from_bytes(mask, byteorder="little")


def mask_int_to_bytes(mask: int, length: int = SIGNAL_MASK_BYTES) -> bytes:
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
        if log_mask is None:
            log_mask = bytes(SIGNAL_MASK_BYTES)
        self._validate_mask(log_mask)

        for name, value in (("sof1_bin", sof1_bin), ("sof2_bin", sof2_bin)):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{name} must fit in one byte")

        self.log_mask = bytes(log_mask)
        self.sof1_bin = sof1_bin
        self.sof2_bin = sof2_bin
        self.signal_list = (FOC_USB_DEBUG_SIGNAL_LIST if signal_list is None else signal_list)
        self.running = False

    @staticmethod
    def _validate_mask(log_mask: bytes | bytearray) -> None:
        if len(log_mask) != SIGNAL_MASK_BYTES:
            raise ValueError(f"log_mask must be {SIGNAL_MASK_BYTES} bytes")

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

        decoder = {
            MsgType.MSG_LOG_DATA: self._decode_log_payload,
            MsgType.MSG_PID_REPLY: self._decode_pid_payload,
            MsgType.MSG_TEXT_REPLY: self._decode_text_payload,
            MsgType.MSG_VAR_REPLY: self._decode_var_payload,
            MsgType.MSG_VERSION_REPLY: self._decode_version_payload,
            MsgType.MSG_STATE_REPLY: self._decode_state_payload,
            MsgType.MSG_MASK_REPLY: self._decode_mask_payload,
            MsgType.MSG_NODE_ID_REPLY: self._decode_node_id_payload,
            MsgType.MSG_ACTIVE_ERRORS_REPLY: self._decode_error_flags_payload,
            MsgType.MSG_LATCHED_ERRORS_REPLY: self._decode_error_flags_payload,
            MsgType.MSG_CAN_HEARTBEAT_REPLY: self._decode_can_heartbeat_payload,
            MsgType.MSG_CAN_ENCODER_RATE_REPLY: self._decode_can_encoder_rate_payload,
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
                MsgType.MSG_GET_VERSION,
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
                MsgType.MSG_GET_CAN_HEARTBEAT,
                MsgType.MSG_GET_CAN_ENCODER_RATE,
                MsgType.MSG_GET_CONTROL_MODE,
            }
            if msg_type not in empty_payload_types:
                raise ValueError(f"{msg_type.name} requires a payload")
            return RawPacket(msg_type_int=int(msg_type), payload_bytes=b"")

        if msg_type == MsgType.MSG_GET_PID and isinstance(data, PIDPayload):
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
            self.set_log_mask(payload)
        elif msg_type == MsgType.MSG_SET_NODE_ID and isinstance(data, NodeIdPayload):
            if not 0 <= int(data.node_id) <= 15:
                raise ValueError("CAN node ID must be between 0 and 15")
            payload = struct.pack("<B", int(data.node_id))
        elif msg_type == MsgType.MSG_SET_CAN_HEARTBEAT and isinstance(data, CanHeartbeatPayload):
            if not 0 <= int(data.rate_cycles) <= 0xFFFF:
                raise ValueError("CAN heartbeat rate must fit in an unsigned 16-bit value")
            payload = struct.pack("<H", int(data.rate_cycles))
        elif msg_type == MsgType.MSG_SET_CAN_ENCODER_RATE and isinstance(data, CanEncoderRatePayload):
            if not 0 <= int(data.rate_cycles) <= 0xFFFF:
                raise ValueError("CAN encoder rate must fit in an unsigned 16-bit value")
            payload = struct.pack("<H", int(data.rate_cycles))
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
    def _decode_version_payload(payload: bytes) -> VersionPayload | None:
        if len(payload) != 3:
            _LOG.warning("Invalid VERSION_REPLY payload length: %d, expected 3", len(payload))
            return None
        return VersionPayload(major=payload[0], minor=payload[1], patch=payload[2])

    @staticmethod
    def _decode_state_payload(payload: bytes) -> StatePayload | None:
        if len(payload) != 1:
            _LOG.warning("Invalid STATE_REPLY payload length: %d, expected 1", len(payload))
            return None
        return StatePayload(state=payload[0])

    def _decode_mask_payload(self, payload: bytes) -> MaskPayload | None:
        if len(payload) != SIGNAL_MASK_BYTES:
            _LOG.warning(
                "Invalid MASK_REPLY payload length: %d, expected %d",
                len(payload),
                SIGNAL_MASK_BYTES,
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
    def _decode_can_heartbeat_payload(payload: bytes) -> CanHeartbeatPayload | None:
        if len(payload) != 2:
            _LOG.warning(
                "Invalid CAN_HEARTBEAT_REPLY payload length: %d, expected 2",
                len(payload),
            )
            return None
        return CanHeartbeatPayload(rate_cycles=struct.unpack("<H", payload)[0])

    @staticmethod
    def _decode_can_encoder_rate_payload(payload: bytes) -> CanEncoderRatePayload | None:
        if len(payload) != 2:
            _LOG.warning(
                "Invalid CAN_ENCODER_RATE_REPLY payload length: %d, expected 2",
                len(payload),
            )
            return None
        return CanEncoderRatePayload(rate_cycles=struct.unpack("<H", payload)[0])

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
        var_meta = next((item for item in VAR_ID_LIST if item["id"] == var_id), None)
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
            (item for item in VAR_ID_LIST if item["id"] == var_payload.var_id), None
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