import queue
import sys
import threading

from PySide6.QtWidgets import QApplication

import config
from protocol_codec import ProtocolCodec, LogPayload, Packet, TextPayload
from protocol_definitions import MsgType
from serial_worker import SerialWorker, SerialConfig, CommandRequest
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow


ERROR_REPLY_TYPES = frozenset({
    MsgType.MSG_UNKNOWN_TYPE,
    MsgType.MSG_INVALID_PAYLOAD,
    MsgType.MSG_UNKNOWN_ID,
    MsgType.MSG_BUFFER_OVERFLOW,
    MsgType.MSG_ERROR,
})


def expected_replies_for(msg_type: MsgType) -> frozenset[MsgType]:
    """Return the successful reply types for each command family."""
    if msg_type == MsgType.MSG_GET_PID:
        return frozenset({MsgType.MSG_PID_REPLY})
    if msg_type == MsgType.MSG_GET_VAR:
        return frozenset({MsgType.MSG_VAR_REPLY})
    if msg_type == MsgType.MSG_GET_VERSION:
        return frozenset({MsgType.MSG_VERSION_REPLY})
    if msg_type == MsgType.MSG_GET_STATE:
        return frozenset({MsgType.MSG_STATE_REPLY})
    if msg_type == MsgType.MSG_TEXT_COMMAND:
        return frozenset({MsgType.MSG_TEXT_REPLY, MsgType.MSG_ACK})
    # SET_PID, SET_VAR, SET_MASK, START/STOP_LOG, flash commands, and
    # SET_STATE are acknowledged by MSG_ACK in the FOC protocol.
    return frozenset({MsgType.MSG_ACK})


from typing import assert_type

def packet_description(packet: Packet) -> str:
    name = getattr(packet.msg_type, "name", str(packet.msg_type))

    if packet.msg_type == MsgType.MSG_SET_MASK:
        data = packet.data
        if not isinstance(data, bytes):
            return f"{name} <unexpected data type for MASK>"
        return f"{name} mask=0x{int.from_bytes(data, 'little'):016X}"

    if packet.msg_type in {MsgType.MSG_GET_PID, MsgType.MSG_SET_PID}:
        if packet.data is None:
            return name
        return f"{name} {packet.data}"

    if packet.msg_type in {MsgType.MSG_GET_VAR, MsgType.MSG_SET_VAR}:
        if packet.data is None:
            return name
        return f"{name} {packet.data}"

    if packet.msg_type == MsgType.MSG_TEXT_COMMAND:
        data = packet.data
        if isinstance(data, TextPayload):
            return f"{name} {data.text!r}"
        return f"{name} <non-TextPayload data>"

    return name


def main():
    app = QApplication(sys.argv)

    plot = PlotWindow()
    codec = ProtocolCodec()
    command_queue: queue.Queue[CommandRequest] = queue.Queue()
    logger = HDF5LogLogger(
        filename=get_log_filename(),
        batch_size=50,
        flush_interval=0.5,
    )

    def enqueue_command(packet: Packet):
        try:
            raw_packet = codec.encode_packet(packet)
            wire_bytes = codec.build_packet(raw_packet)
            request = CommandRequest(
                packet=packet,
                wire_bytes=wire_bytes,
                expected_replies=expected_replies_for(packet.msg_type),
                description=packet_description(packet),
            )
            command_queue.put(request)
        except Exception as exc:
            plot.enqueue_command_result(None, None, False, str(exc), packet)

    plot.set_command_sender(enqueue_command)

    def on_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        worker = plot.worker
        for raw_packet in packets:
            decoded = codec.decode_packet(raw_packet)
            if decoded is None:
                continue

            if decoded.msg_type == MsgType.MSG_LOG_DATA and isinstance(decoded.data, LogPayload):
                logger.enqueue(decoded.data)
                plot.enqueue_log(decoded.data)

            if worker is not None:
                worker.handle_reply(decoded)
            plot.enqueue_reply(decoded)

    def on_command_result(request, reply, ok: bool, reason: str):
        if (
            ok
            and request is not None
            and request.packet.msg_type == MsgType.MSG_SET_MASK
            and isinstance(request.packet.data, (bytes, bytearray))
        ):
            codec.set_log_mask(request.packet.data)
        plot.enqueue_command_result(request, reply, ok, reason)

    def stop_serial(join: bool = True):
        worker = plot.worker
        if worker is None:
            return

        worker.stop()
        plot.set_worker(None)

        if join and threading.current_thread() is not worker.thread:
            worker.join()

    def worker_disconnect():
        stop_serial(join=False)

    def start_serial(port: str):
        stop_serial(join=True)

        worker = SerialWorker(
            config=SerialConfig(
                port=port,
                baudrate=config.BAUDRATE,
                timeout=config.TIMEOUT,
                command_interval=config.COMMAND_RATE_LIMIT_S,
                reply_timeout=config.COMMAND_REPLY_TIMEOUT_S,
            ),
            command_queue=command_queue,
            data_callback=on_data,
            error_callback=lambda e: plot.enqueue_serial_error(e),
            disconnect_callback=worker_disconnect,
            command_result_callback=on_command_result,
        )
        plot.set_worker(worker)
        worker.start()

    plot.set_start_serial_callback(start_serial)
    if hasattr(plot, "connect_dock"):
        plot.connect_dock.on_disconnect = lambda: stop_serial(join=True)

    plot.show()

    try:
        return app.exec()
    finally:
        stop_serial(join=True)
        logger.stop()
        logger.join()


if __name__ == "__main__":
    raise SystemExit(main())
