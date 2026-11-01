"""Bootloader entry over the stable command IDs, without device discovery."""
import time
import serial
from config import SERIAL_BAUDRATE, SERIAL_TIMEOUT
from protocol_codec import ProtocolCodec, RawPacket, CommandErrorPayload
from protocol_definitions import MsgType, FOCState, ERROR_REPLY_TYPES


def request_bootloader(port, *, serial_factory=serial.Serial, timeout=8.0,
                       clock=time.monotonic, pause=time.sleep):
    """Stop output and request bootloader on an exclusively owned serial port.

    Called on a background thread after the normal transport has closed. No
    device-info request, definition catalog, or matching protocol is needed.
    Success means the firmware acknowledged the bootloader request.
    """
    if not port:
        raise ValueError("Select a physical serial port first.")
    codec = ProtocolCodec()
    buffer = bytearray()
    deadline = clock() + timeout
    with serial_factory(port=port, baudrate=SERIAL_BAUDRATE,
                        timeout=min(SERIAL_TIMEOUT, 0.1), write_timeout=1.0) as connection:
        connection.reset_input_buffer()

        def exchange(command, payload=b"", expected=MsgType.MSG_ACK):
            frame = codec.build_packet(RawPacket(command, payload))
            if connection.write(frame) != len(frame):
                raise OSError("Incomplete serial write while requesting bootloader.")
            while clock() < deadline:
                buffer.extend(connection.read(256))
                for raw in codec.extract_packets(buffer):
                    if raw.msg_type_int in ERROR_REPLY_TYPES:
                        error = CommandErrorPayload.decode(raw.payload_bytes)
                        if error.command in (int(command), 0xFF):
                            raise RuntimeError(str(error))
                    elif raw.msg_type_int == expected:
                        required_length = 1 if expected == MsgType.MSG_STATE_REPLY else 0
                        if len(raw.payload_bytes) != required_length:
                            raise ValueError("Malformed recovery command reply.")
                        return raw.payload_bytes
            raise TimeoutError(f"Timed out waiting for {expected.name} during bootloader entry.")

        exchange(MsgType.MSG_STOP_LOG)
        exchange(MsgType.MSG_SET_STATE, bytes((FOCState.FOC_STATE_STOP,)))
        while clock() < deadline:
            state = exchange(MsgType.MSG_GET_STATE, expected=MsgType.MSG_STATE_REPLY)[0]
            if state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR):
                exchange(MsgType.MSG_ENTER_BOOTLOADER)
                return "Bootloader request accepted. Reconnect after flashing the firmware."
            if state != FOCState.FOC_STATE_STOP:
                raise RuntimeError("Driver did not stop; bootloader entry was not requested.")
            pause(0.05)
        raise TimeoutError("Motor did not reach IDLE/ERROR before the bootloader timeout.")
