"""Headless app smoke: recover bootloader after a real version-mismatch flow."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from USB_debugger import debugger_main
from USB_debugger.protocol_definitions import PROTOCOL_VERSION
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

windows, workers, recovered = [], [], []

class MismatchedSerial(debugger_main.SimulatorWorker):
    is_simulator = False
    def __init__(self, config, **kwargs):
        super().__init__(config=debugger_main.SimulatorConfig(), **kwargs)
        self.info = replace(self.info, protocol=PROTOCOL_VERSION - 1)
        workers.append(self)

def recover(endpoint):
    assert endpoint == "COM9"
    assert windows[0].worker is None
    assert workers and not workers[0].thread.is_alive(), "Serial transport was not closed first"
    recovered.append(endpoint)
    return "Bootloader request accepted."

class RecoveryWindow(debugger_main.PlotWindow):
    def __init__(self):
        super().__init__()
        windows.append(self)
    def set_start_connection_callback(self, callback):
        super().set_start_connection_callback(callback)
        QTimer.singleShot(0, lambda: callback("COM9"))
        QTimer.singleShot(50, self.try_recovery)
        QTimer.singleShot(5000, QApplication.instance().quit)
    def try_recovery(self):
        if "Incompatible protocol" not in self.command_dock.command_log.toPlainText():
            QTimer.singleShot(25, self.try_recovery)
            return
        assert self.connect_dock.bootloader_btn.isEnabled()
        self.connect_dock.bootloader_btn.click()
        QTimer.singleShot(25, self.check_result)
    def check_result(self):
        if "accepted" not in self.connect_dock.status_label.text():
            QTimer.singleShot(25, self.check_result)
            return
        assert not self.connect_dock.bootloader_btn.is_busy
        QApplication.instance().quit()

with tempfile.TemporaryDirectory() as directory:
    with patch.dict(os.environ, {"RAPID_DRIVE_CACHE_DIR": directory}), \
         patch.object(debugger_main, "PlotWindow", RecoveryWindow), \
         patch.object(debugger_main, "SerialWorker", MismatchedSerial), \
         patch.object(debugger_main, "request_bootloader", recover), \
         patch("USB_debugger.ui.connect_panel.SerialWorker.list_available_ports", return_value=["COM9"]):
        assert debugger_main.main() == 0
assert recovered == ["COM9"]
assert windows[0].worker is None
print("PASS: version mismatch closes normal transport; connection-panel recovery runs without discovery")
