"""Headless simulator/GUI/HDF5 smoke test; requires application dependencies."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from USB_debugger import debugger_main
from USB_debugger.protocol_definitions import CommandErrorCode, PROTOCOL_VERSION
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import h5py


windows = []
connection_checks = []
rejected_states = []
busy_checks = []


class RejectRunSimulator(debugger_main.SimulatorWorker):
    def _handle_packet(self, packet):
        if packet.msg_type_int == debugger_main.MsgType.MSG_SET_STATE:
            rejected_states.append(packet.payload_bytes)
            self._error(debugger_main.MsgType.MSG_ERROR, packet.msg_type_int,
                        CommandErrorCode.ENCODER_NOT_ALIGNED, "Encoder is not aligned: test rejection.")
        else:
            super()._handle_packet(packet)


class SmokeWindow(debugger_main.PlotWindow):
    def __init__(self):
        super().__init__()
        windows.append(self)
        self.signal_dock.saved_names = ["tick"]
        self.transport_ready.connect(lambda rate: self.logging_toggle_callback(True))
        self.transport_ready.connect(self.reject_run)

    def reject_run(self, rate):
        QTimer.singleShot(100, self.click_run)
        QTimer.singleShot(600, self.check_connection)

    def click_run(self):
        self.basics_dock.run_state_button.click()
        busy_checks.append(self.basics_dock.run_state_button.is_busy)

    def check_connection(self):
        # Wait for the GUI-thread error callback, not a fixed worker deadline.
        if ("ENCODER_NOT_ALIGNED" not in self.command_dock.command_log.toPlainText()
                or self.basics_dock.run_state_button.is_busy):
            QTimer.singleShot(50, self.check_connection)
            return
        connection_checks.append(bool(rejected_states) and self.worker is not None and self._transport_ready)
        QApplication.instance().quit()

    def set_start_connection_callback(self, callback):
        super().set_start_connection_callback(callback)
        QTimer.singleShot(0, lambda: callback(debugger_main.SIMULATOR_ENDPOINT))
        # The timeout is a guard; normal completion is relative to readiness.
        QTimer.singleShot(5000, QApplication.instance().quit)


with tempfile.TemporaryDirectory() as directory:
    filename = Path(directory) / "smoke.h5"
    with patch.dict(os.environ, {"RAPID_DRIVE_CACHE_DIR": str(Path(directory) / "cache")}), \
         patch.object(debugger_main, "PlotWindow", SmokeWindow), \
         patch.object(debugger_main, "SimulatorWorker", RejectRunSimulator), \
         patch.object(debugger_main, "get_log_filename", lambda: filename):
        result = debugger_main.main()
    assert result == 0
    assert rejected_states == [b"\x0c"]
    assert connection_checks == [True], "RUN rejection disconnected the GUI session"
    assert busy_checks == [True], "Clicked command did not enter busy state"
    terminal = windows[0].command_dock.command_log.toPlainText()
    assert "ENCODER_NOT_ALIGNED" in terminal and "Encoder is not aligned: test rejection." in terminal
    assert "MSG_ACK" not in terminal and "PIDPayload" not in terminal and "VarPayload" not in terminal
    assert windows[0].worker is None
    assert windows[0].plot_panel.active_signals == ("tick",)
    with h5py.File(filename) as log:
        assert log.attrs["protocol_version"] == PROTOCOL_VERSION
        assert log.attrs["sample_rate_hz"] == 8000
        assert log.attrs["firmware_version"] == "1.0.0"
        assert len(log["log/tick"]) > 0
        import json
        assert len(json.loads(log.attrs["definitions"])["variables"]) == 19
        assert len(log.attrs["definitions_fingerprint"]) == 16
    print("PASS: busy buttons, quiet replies, visible errors, connected telemetry, HDF5 metadata, and clean shutdown")
