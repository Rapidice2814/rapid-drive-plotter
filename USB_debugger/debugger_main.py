import logging
import queue
import sys
import threading
from pathlib import Path

# Support both direct execution (python USB_debugger/debugger_main.py) and package imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEBUGGER_DIR = Path(__file__).resolve().parent
for _import_path in (str(_PROJECT_ROOT), str(_DEBUGGER_DIR)):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)
del _import_path

from PySide6.QtWidgets import QApplication

from protocol_codec import LogPayload, Packet, ProtocolCodec
from protocol_definitions import MsgType
from serial_worker import SerialConfig, SerialWorker
from hdf5_logger import HDF5LogLogger, get_log_filename
from plot_window import PlotWindow

from config import (
    SERIAL_BAUDRATE,
    SERIAL_TIMEOUT,
    SIMULATOR_ENDPOINT,
    SIMULATOR_SAMPLES_PER_PACKET,
    SAMPLE_RATE,
)
from Serial_Simulator.simulator_worker import SimulatorConfig, SimulatorWorker

_LOG = logging.getLogger(__name__)


def main():
    app = QApplication(sys.argv)

    plot = PlotWindow()
    codec = ProtocolCodec()
    command_queue: queue.Queue[bytes] = queue.Queue()

    active_logger: HDF5LogLogger | None = None
    logger_lock = threading.Lock()

    def set_logging_enabled(enabled: bool) -> None:
        """Start a fresh log on enable and drain/close it on disable."""
        nonlocal active_logger
        if enabled:
            with logger_lock:
                if active_logger is not None:
                    return
                logger = HDF5LogLogger(
                    filename=get_log_filename(),
                    batch_size=50,
                    flush_interval=0.5,
                )
                logger.start()
                active_logger = logger
            _LOG.info("Telemetry logging enabled: %s", logger.filename)
            return

        with logger_lock:
            logger = active_logger
            active_logger = None
            if logger is not None:
                logger.stop()
        if logger is not None:
            logger.join()
            _LOG.info("Telemetry logging disabled")

    plot.set_logging_toggle_callback(set_logging_enabled)

    plot.set_command_sender(
        lambda pkt: command_queue.put(codec.build_packet(codec.encode_packet(pkt)))
    )

    def on_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        for packet in packets:
            decoded = codec.decode_packet(packet)
            if decoded is not None and isinstance(decoded.data, LogPayload):
                # Serialize the logger reference and enqueue with toggle/stop so
                # no payload can race in after the logger's drain has finished.
                with logger_lock:
                    if active_logger is not None:
                        active_logger.enqueue(decoded.data)
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

        # Capture and encode the selected mask on the GUI thread; worker threads
        # must not access Qt widgets. The callbacks run only once the transport
        # is ready and its stale command queue has been cleared.
        selected_mask = bytes(plot.signal_dock.current_mask)
        initialization_packets = (
            Packet(msg_type=MsgType.MSG_STOP_LOG, data=None),
            Packet(msg_type=MsgType.MSG_SET_MASK, data=selected_mask),
            Packet(msg_type=MsgType.MSG_START_LOG, data=None),
        )
        initialization_commands = tuple(
            codec.build_packet(codec.encode_packet(packet))
            for packet in initialization_packets
        )

        def on_worker_ready():
            for command in initialization_commands:
                command_queue.put(command)

        common_callbacks = {
            "command_queue": command_queue,
            "data_callback": on_data,
            "error_callback": lambda error: _LOG.error("Connection error: %s", error),
            "disconnect_callback": worker_disconnect,
            "ready_callback": on_worker_ready,
        }
        if endpoint == SIMULATOR_ENDPOINT:
            worker = SimulatorWorker(
                **common_callbacks,
                config=SimulatorConfig(
                    samples_per_packet=SIMULATOR_SAMPLES_PER_PACKET,
                    sample_rate=SAMPLE_RATE,
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
        set_logging_enabled(False)


if __name__ == "__main__":
    raise SystemExit(main())
