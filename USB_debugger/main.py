import logging
import sys
import queue
import threading
from pathlib import Path

# Support both direct execution (python USB_debugger/main.py) and package imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEBUGGER_DIR = Path(__file__).resolve().parent
for _import_path in (str(_PROJECT_ROOT), str(_DEBUGGER_DIR)):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)
del _import_path

from PySide6.QtWidgets import QApplication

from protocol_codec import ProtocolCodec, LogPayload
from serial_worker import SerialWorker, SerialConfig
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow

from config import (
    SERIAL_BAUDRATE,
    SERIAL_TIMEOUT,
    SIMULATOR_ENDPOINT,
    SIMULATOR_SAMPLE_INTERVAL,
    SIMULATOR_SAMPLES_PER_PACKET,
    TIMESTAMP_HZ,
)
from Serial_Simulator.simulator_worker import SimulatorConfig, SimulatorWorker

_LOG = logging.getLogger(__name__)


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
    logger.start()

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
                plot.enqueue_reply(decoded)

    def stop_worker(join: bool = True):
        worker = plot.worker
        if worker is None:
            return

        worker.stop()
        plot.set_worker(None)

        if join and threading.current_thread() is not worker.thread:
            worker.join()

    def worker_disconnect():
        # Signal emission is thread-safe; the connected PlotWindow slot updates
        # connection widgets on the GUI thread.
        plot.serial_disconnected.emit()

    def start_connection(endpoint: str):
        stop_worker(join=True)

        common_callbacks = {
            "command_queue": command_queue,
            "data_callback": on_data,
            "error_callback": lambda error: _LOG.error("Connection error: %s", error),
            "disconnect_callback": worker_disconnect,
        }
        if endpoint == SIMULATOR_ENDPOINT:
            worker = SimulatorWorker(
                **common_callbacks,
                config=SimulatorConfig(
                    sample_interval=SIMULATOR_SAMPLE_INTERVAL,
                    samples_per_packet=SIMULATOR_SAMPLES_PER_PACKET,
                    timestamp_hz=TIMESTAMP_HZ,
                ),
            )
        else:
            worker = SerialWorker(
                config=SerialConfig(port=endpoint, baudrate=SERIAL_BAUDRATE, timeout=SERIAL_TIMEOUT),
                **common_callbacks,
            )
        plot.set_worker(worker)
        worker.start()

    plot.set_start_connection_callback(start_connection)
    if hasattr(plot, "connect_dock"):
        plot.connect_dock.on_disconnect = lambda: stop_worker(join=True)

    plot.show()

    try:
        return app.exec()
    finally:
        stop_worker(join=True)
        logger.stop()
        logger.join()


if __name__ == "__main__":
    raise SystemExit(main())