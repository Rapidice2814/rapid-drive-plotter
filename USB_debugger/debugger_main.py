import logging
import json
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
from PySide6.QtCore import QTimer
from protocol_session import ProtocolSession

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

    def recording_metadata():
        metadata = {"sample_rate_hz": session.sample_rate,
                    "firmware_version": ".".join(map(str, session.firmware_version or ())) or "unknown"}
        if session.definitions is not None and session.info is not None:
            metadata.update(definitions=json.dumps(session.definitions.as_dict()),
                            signal_definitions=json.dumps(session.definitions.signals),
                            definitions_fingerprint=f"{session.info.fingerprint:016x}")
        return metadata

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
                    metadata=recording_metadata(),
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

    def session_failed(message):
        _LOG.error(message)
        plot.setWindowTitle("Serial Plotter — " + message)
        stop_worker(join=True)
        plot.enqueue_text(message)

    def session_ready():
        with logger_lock:
            if active_logger is not None:
                active_logger.metadata.update(recording_metadata())
        plot.connection_ready.emit((plot.worker, float(session.sample_rate)))

    def command_rejected(message):
        _LOG.warning(message)
        plot.enqueue_text(message)

    command_rejections = queue.Queue()
    session = ProtocolSession(codec, command_queue.put, session_ready,
                              session_failed, SAMPLE_RATE,
                              logging_changed=plot.logging_confirmed.emit,
                              rejected=command_rejections.put,
                              definitions_ready=lambda definitions, info, mask:
                                  plot.definitions_received.emit((definitions, info, mask, plot.worker)))
    # Rejections, failures, and timeouts are delivered to the GUI thread.
    failures = queue.Queue()
    session.failed_callback = lambda message: failures.put(message)
    def poll_session():
        session.tick()
        while True:
            try:
                message = command_rejections.get_nowait()
            except queue.Empty:
                break
            command_rejected(message)
        try:
            message = failures.get_nowait()
        except queue.Empty:
            return
        session_failed(message)
    session_timer = QTimer(app)
    session_timer.timeout.connect(poll_session)
    session_timer.start(100)
    plot.set_command_sender(session.send, supports_completion=True)

    def on_data(rx_buffer: bytearray):
        with session.lock:
            if not session.active:
                rx_buffer.clear()
                return
            handle_data(rx_buffer)

    def handle_data(rx_buffer: bytearray):
        packets = codec.extract_packets(rx_buffer)
        for packet in packets:
            session.receive(packet)
            if not session.active:
                return
            if packet.msg_type_int == MsgType.MSG_DEFINITIONS_REPLY:
                continue
            if packet.msg_type_int == MsgType.MSG_LOG_DATA and session.initializing:
                continue
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
        session.stop()
        worker = plot.worker
        if worker is None:
            return

        worker.stop()
        plot.set_worker(None)

        if join and threading.current_thread() is not worker.thread:
            worker.join()

    def start_connection(endpoint: str):
        stop_worker(join=True)
        if active_logger is not None:
            set_logging_enabled(False)
            set_logging_enabled(True)
        plot.setWindowTitle("Serial Plotter")
        while not failures.empty():
            failures.get_nowait()
        while not command_rejections.empty():
            command_rejections.get_nowait()

        # Capture names on the GUI thread. IDs and mask width are resolved only
        # after the worker has read device info and validated definitions.
        selected_names = plot.signal_dock.selected_names()
        def on_worker_ready():
            session.start(selected_names)

        worker_holder: dict[str, object] = {}

        def worker_disconnect():
            session.stop()
            # Tag the notification so delayed events from an old connection
            # cannot mark a newer connection as failed.
            disconnecting_worker = worker_holder.get("worker")
            if disconnecting_worker is not None:
                plot.serial_disconnected.emit(disconnecting_worker)

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
        worker_holder["worker"] = worker
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
