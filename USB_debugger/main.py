import sys
import queue
import threading

from PySide6.QtWidgets import QApplication

from protocol_codec import ProtocolCodec, LogPayload
from serial_worker import SerialWorker, SerialConfig
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow


def main():
    app = QApplication(sys.argv)

    plot = PlotWindow()
    codec = ProtocolCodec()
    command_queue: queue.Queue[bytes] = queue.Queue()
    logger = HDF5LogLogger(
        filename=get_log_filename(),
        batch_size=50,
        flush_interval=0.5,
    )

    plot.set_command_sender(
        lambda pkt: command_queue.put(codec.build_packet(codec.encode_packet(pkt)))
    )

    def on_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        for packet in packets:
            decoded = codec.decode_packet(packet)
            if decoded is not None and isinstance(decoded.data, LogPayload):
                logger.enqueue(decoded.data)
                plot.enqueue_log(decoded.data)
            if decoded is not None:
                plot.on_reply(decoded)

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
            config=SerialConfig(port=port, baudrate=115200, timeout=0.1),
            command_queue=command_queue,
            data_callback=on_data,
            error_callback=lambda e: print(f"Serial error: {e}"),
            disconnect_callback=worker_disconnect,
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