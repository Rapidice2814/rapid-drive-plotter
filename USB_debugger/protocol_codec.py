import struct
from dataclasses import dataclass
from typing import Any

from protocol_definitions import SOF1_BIN, SOF2_BIN, MsgType, FOC_USB_DEBUG_SIGNAL_LIST, VAR_ID_LIST


@dataclass
class RawPacket:
    msg_type: int
    payload: bytes

@dataclass
class Packet:
    msg_type: MsgType
    data: LogPayload | PIDPayload | TextPayload | VarPayload | int | None

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


class ProtocolCodec:
    def __init__(
        self,
        log_mask=0,
        sof1_bin=SOF1_BIN,
        sof2_bin=SOF2_BIN,
        signal_list=FOC_USB_DEBUG_SIGNAL_LIST,
    ):
        self.log_mask = log_mask
        self.sof1_bin = sof1_bin
        self.sof2_bin = sof2_bin
        self.signal_list = signal_list
        self.running = False

    @staticmethod
    def u32_to_f32(v):
        return struct.unpack('<f', struct.pack('<I', v))[0]

    @staticmethod
    def u32_to_i32(v):
        return struct.unpack('<i', struct.pack('<I', v))[0]

    @classmethod
    def cast_u32_value(cls, v, typ):
        if typ == 'u32':
            return v
        elif typ == 'i32':
            return cls.u32_to_i32(v)
        elif typ == 'f':
            return cls.u32_to_f32(v)
        else:
            raise ValueError(f"Unsupported type: {typ}")

    @staticmethod
    def f32_to_u32(v):
        return struct.unpack('<I', struct.pack('<f', float(v)))[0]


    @staticmethod
    def i32_to_u32(v):
        return struct.unpack('<I', struct.pack('<i', int(v)))[0]


    @classmethod
    def cast_to_u32_value(cls, v, typ):
        if typ == 'u32':
            return int(v)
        elif typ == 'i32':
            return cls.i32_to_u32(v)
        elif typ == 'f':
            return cls.f32_to_u32(v)
        else:
            raise ValueError(f"Unsupported type: {typ}")

    def set_log_mask(self, log_mask):
        self.log_mask = log_mask

    def get_enabled_signals(self, log_mask=None):
        if log_mask is None:
            log_mask = self.log_mask

        enabled = []
        for sig in self.signal_list:
            if log_mask & (1 << sig["bit"]):
                enabled.append(sig)
        return enabled

    def extract_packets(self, buffer: bytearray) -> list[RawPacket]:
        packets: list[RawPacket] = []
        header_len = 5  # 2 SOF + 1 msg_type + 2 payload_length

        while True:
            if len(buffer) < header_len:
                break

            sof_bin_index = buffer.find(bytes([self.sof1_bin, self.sof2_bin]))
            if sof_bin_index == -1:
                buffer.clear()
                break

            if sof_bin_index > 0:
                del buffer[:sof_bin_index]

            msg_type = buffer[2]
            payload_length = buffer[3] | (buffer[4] << 8)
            packet_length = header_len + payload_length

            if len(buffer) < packet_length:
                break

            payload = bytes(buffer[5:5 + payload_length])

            packets.append(RawPacket(
                msg_type=msg_type,
                payload=payload,
            ))

            del buffer[:packet_length]

        return packets

    def build_packet(self, packet: RawPacket) -> bytes:
            msg_type = int(packet.msg_type)
    
            if not (0 <= msg_type <= 0xFF):
                raise ValueError("msg_type must fit in one byte")
    
            payload_length = len(packet.payload)
            if payload_length > 0xFFFF:
                raise ValueError("payload too large for 16-bit length")
    
            return struct.pack("<BBBH", self.sof1_bin, self.sof2_bin, msg_type, payload_length) + packet.payload

    def decode_packet(self, packet: RawPacket) -> Packet | None:
        try:
            msg_type = MsgType(packet.msg_type)
        except ValueError:
            print(f"Unknown packet type: {packet.msg_type}")
            return None

        match msg_type:
            case MsgType.MSG_LOG_DATA:
                decoded = self._decode_log_payload(packet.payload)
            case MsgType.MSG_PID_REPLY:
                decoded = self._decode_pid_payload(packet.payload)
            case MsgType.MSG_TEXT_REPLY:
                decoded = self._decode_text_payload(packet.payload)
            case MsgType.MSG_VAR_REPLY:
                decoded = self._decode_var_payload(packet.payload)
            case _:
                print(f"Received packet: {msg_type.name}")
                return None

        if decoded is None:
            return None

        return Packet(
            msg_type=msg_type,
            data=decoded,
        )

    def encode_packet(self, packet: Packet) -> RawPacket:
        payload = b""

        if packet.data is None:
            payload = b""
        elif packet.msg_type == MsgType.MSG_GET_PID and isinstance(packet.data, PIDPayload):
            payload = struct.pack('<B', packet.data.controller_id)
        elif packet.msg_type == MsgType.MSG_SET_PID and isinstance(packet.data, PIDPayload):
            payload = struct.pack('<Bfff', packet.data.controller_id, packet.data.kp, packet.data.ki, packet.data.kd)
        elif packet.msg_type == MsgType.MSG_TEXT_COMMAND and isinstance(packet.data, TextPayload):
            payload = self._encode_text_payload(packet.data)
        elif packet.msg_type == MsgType.MSG_SET_MASK and isinstance(packet.data, int):
            self.log_mask = packet.data
            payload = struct.pack('<I', packet.data)
        elif packet.msg_type == MsgType.MSG_GET_VAR and isinstance(packet.data, VarPayload):
            payload = struct.pack('<B', packet.data.var_id)
        elif packet.msg_type == MsgType.MSG_SET_VAR and isinstance(packet.data, VarPayload):
            payload = self._encode_var_payload(packet.data)


        return RawPacket(
            msg_type=int(packet.msg_type),
            payload=payload,
        )


    def _decode_log_payload(self, payload: bytes, log_mask: int | None = None) -> LogPayload | None:
        if log_mask is None:
            log_mask = self.log_mask

        payload_header_format = '<IHH'
        payload_header_size = struct.calcsize(payload_header_format)

        if len(payload) < payload_header_size:
            return None

        timestamp, sample_count, signal_count = struct.unpack_from(payload_header_format, payload, 0)

        enabled_signals = self.get_enabled_signals(log_mask)

        if len(enabled_signals) != signal_count:
            print(f"Mask enables {len(enabled_signals)} signals, but payload says signal_count={signal_count}")
            return None

        data_count = sample_count * signal_count
        data_format = f'<{data_count}I'
        expected_size = payload_header_size + struct.calcsize(data_format)

        if len(payload) != expected_size:
            return None

        raw_data = struct.unpack_from(data_format, payload, payload_header_size)

        signal_buffers: dict[str, list[int | float]] = {}
        for sig in enabled_signals:
            signal_buffers[sig["name"]] = []

        for sample_idx in range(sample_count):
            base_idx = sample_idx * signal_count
            for sig_idx, sig in enumerate(enabled_signals):
                raw_u32 = raw_data[base_idx + sig_idx]
                value = self.cast_u32_value(raw_u32, sig["type"])
                signal_buffers[sig["name"]].append(value)

        return LogPayload(
            timestamp=timestamp,
            sample_count=sample_count,
            signal_count=signal_count,
            enabled_signals=enabled_signals,
            signals=signal_buffers,
        )
            
    
    def _decode_pid_payload(self, payload: bytes) -> PIDPayload | None:
        if len(payload) != 13:
            print(f"Invalid PID_REPLY payload length: {len(payload)}")
            return None

        controller_id = payload[0]
        gains_data = payload[1:13]
        kp, ki, kd = struct.unpack('<fff', gains_data)

        return PIDPayload(
            controller_id=controller_id,
            kp=kp,
            ki=ki,
            kd=kd,
        )

    def _decode_var_payload(self, payload: bytes) -> VarPayload | None:
        if len(payload) != 5:
            print(f"Invalid VAR_REPLY payload length: {len(payload)}")
            return None

        var_id = payload[0]
        raw_value = struct.unpack_from("<I", payload, 1)[0]

        var_meta = next((v for v in VAR_ID_LIST if v["id"] == var_id), None)
        if var_meta is None:
            print(f"Unknown var_id: {var_id}")
            return None

        try:
            value = self.cast_u32_value(raw_value, var_meta["type"])
        except ValueError as exc:
            print(exc)
            return None

        return VarPayload(var_id=var_id, value=value)

    def _encode_var_payload(self, var_payload: VarPayload) -> bytes:
        var_meta = next((v for v in VAR_ID_LIST if v["id"] == var_payload.var_id), None)
        if var_meta is None:
            raise ValueError(f"Unknown var_id: {var_payload.var_id}")

        if var_payload.value is None:
            raise ValueError(f"Var value is None for var_id: {var_payload.var_id}")

        raw_value = self.cast_to_u32_value(var_payload.value, var_meta["type"])
        return struct.pack("<BI", var_payload.var_id, raw_value)

    def _encode_text_payload(self, text_payload: TextPayload) -> bytes:
        return (text_payload.text + "\n").encode("utf-8")
    
    def _decode_text_payload(self, payload: bytes) -> TextPayload | None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            print("Failed to decode text payload as UTF-8")
            return None

        return TextPayload(text=text)


    def build_text_command_packet(self, command_str: str) -> bytes:
        cmd_bytes = self.build_packet(RawPacket(MsgType.MSG_TEXT_COMMAND, command_str.encode("utf-8")))
        return cmd_bytes

    # def build_text_command_packet(self, command_str: str) -> bytes:
    #     command_str = command_str.strip()

    #     if not command_str:
    #         return self.build_packet(RawPacket(MsgType.MSG_START_LOG, b""))

    #     parts = command_str.split()
    #     cmd = parts[0].lower()

    #     if cmd == "start":
    #         self.running = True
    #         return self.build_packet(RawPacket(MsgType.MSG_START_LOG, b""))

    #     if cmd == "stop":
    #         self.running = False
    #         return self.build_packet(RawPacket(MsgType.MSG_STOP_LOG, b""))

    #     if cmd == "setmask":
    #         if self.running:
    #             raise ValueError("Cannot change log mask while logging is running. Please stop logging first.")

    #         if len(parts) != 2:
    #             raise ValueError("Usage: setmask <value>")

    #         try:
    #             new_mask = int(parts[1], 0)
    #         except ValueError as exc:
    #             raise ValueError("Invalid mask value. Examples: setmask 3, setmask 0x03") from exc

    #         self.log_mask = new_mask
    #         return self.build_packet(RawPacket(MsgType.MSG_SET_MASK, struct.pack("<I", new_mask)))

    #     if cmd == "getpid":
    #         if len(parts) != 2:
    #             raise ValueError("Usage: getpid <controller_id>")

    #         try:
    #             controller_id = int(parts[1], 0)
    #         except ValueError as exc:
    #             raise ValueError("Invalid controller ID. Examples: getpid 1, getpid 0x01") from exc

    #         return self.build_packet(RawPacket(MsgType.MSG_GET_PID, struct.pack("<B", controller_id)))

    #     if cmd == "setpid":
    #         if len(parts) != 5:
    #             raise ValueError("Usage: setpid <controller_id> <kp> <ki> <kd>")

    #         try:
    #             controller_id = int(parts[1], 0)
    #             kp = float(parts[2])
    #             ki = float(parts[3])
    #             kd = float(parts[4])
    #         except ValueError as exc:
    #             raise ValueError("Invalid arguments. Examples: setpid 1 0.1 0.01 0.001") from exc

    #         return self.build_packet(RawPacket(MsgType.MSG_SET_PID, struct.pack("<Bfff", controller_id, kp, ki, kd)))

    #     return command_str.encode("utf-8")

    #     raise ValueError(
    #         f"Unknown command: {command_str}. Commands: start, stop, setmask <value>, "
    #         "getpid <controller_id>, setpid <controller_id> <kp> <ki> <kd>"
    #     )