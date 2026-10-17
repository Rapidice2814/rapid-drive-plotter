"""Optional Qt checks for runtime controls and name-based setpoints."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "USB_debugger"), str(ROOT)]
HAS_GUI = all(importlib.util.find_spec(name) for name in ("PySide6", "pyqtgraph", "serial"))

if HAS_GUI:
    from PySide6.QtWidgets import QApplication
    from plot_window import PlotWindow
    from protocol_definitions import MsgType, FOCState, PROTOCOL_VERSION
    from device_definitions import DeviceInfo
    from test_protocol import fixture, definitions_from


@unittest.skipUnless(HAS_GUI, "Install application dependencies to run Qt tests")
class DynamicUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = PlotWindow()
        self.sent = []
        self.window.set_command_sender(self.sent.append)
        self.window.set_worker(object())
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.set_worker(None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def populate(self, data):
        definitions = definitions_from(data)
        counts = tuple(map(len, definitions.lists()))
        info = DeviceInfo(PROTOCOL_VERSION, (0, 3, 0), counts, (counts[0] + 7) // 8,
                          min(8, counts[0]), 7999, 123, 1019, definitions.fingerprint())
        mask = definitions.mask_for_names(["tick"], info.max_signals)
        self.window._handle_definitions((definitions, info, mask, self.window.worker))
        return definitions

    def test_controls_are_empty_until_discovery(self):
        self.assertEqual(self.window.signal_dock.signal_checkboxes, {})
        self.assertEqual(self.window.pid_dock.pid_widgets, {})
        self.assertEqual(self.window.var_dock.var_widgets, {})
        self.assertFalse(self.window.setpoint_dock.mode_buttons["speed"].isEnabled())

    def test_firmware_version_refresh_uses_device_info(self):
        from protocol_codec import Packet
        self.populate(fixture())
        self.window._handle_transport_ready(7999.0)
        self.assertIn(MsgType.MSG_GET_DEVICE_INFO, [packet.msg_type for packet in self.sent])
        self.assertTrue(self.window.basics_dock.version_reply_timeout_timer.isActive())
        definitions = definitions_from(fixture())
        counts = tuple(map(len, definitions.lists()))
        info = DeviceInfo(PROTOCOL_VERSION, (0, 3, 1), counts, 5, 8, 7999, 123, 1019, definitions.fingerprint())
        self.window.on_reply(Packet(MsgType.MSG_DEVICE_INFO_REPLY, info))
        self.assertEqual(self.window.basics_dock.version_label.text(), "0.3.1")
        self.assertFalse(self.window.basics_dock.version_reply_timeout_timer.isActive())

    def test_populates_all_lists_and_resolves_reordered_setpoints(self):
        data = fixture()
        data["variables"].reverse()
        data["pids"].reverse()
        definitions = self.populate(data)
        self.assertEqual(len(self.window.signal_dock.signal_checkboxes), 36)
        self.assertEqual(len(self.window.pid_dock.pid_widgets), 4)
        self.assertEqual(len(self.window.var_dock.var_widgets), 19)
        self.assertEqual(self.window.signal_dock.mask_bytes, 5)
        self.assertFalse(self.window.signal_dock.read_mask_button.isEnabled())
        self.window._handle_transport_ready(7999.0)
        dock = self.window.setpoint_dock
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        self.sent.clear()
        dock._activate_mode("speed")
        reads = [packet.data.var_id for packet in self.sent if packet.msg_type == MsgType.MSG_GET_VAR]
        speed_id = next(entry["id"] for entry in definitions.variables if entry["name"] == "speed_setpoint")
        self.assertEqual(reads, [speed_id])
        dock.value_widgets["speed_setpoint"].setValue(12.5)
        dock._send_setpoint("speed_setpoint")
        self.assertEqual(self.sent[-1].data.var_id, speed_id)
        self.assertEqual(self.sent[-1].data.value, 12.5)

    def test_missing_setpoint_disables_only_its_mode(self):
        data = fixture()
        data["variables"] = [entry for entry in data["variables"] if entry["name"] != "speed_setpoint"]
        self.populate(data)
        self.window._handle_transport_ready(7999.0)
        dock = self.window.setpoint_dock
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        self.assertFalse(dock.mode_buttons["speed"].isEnabled())
        self.assertTrue(dock.mode_buttons["position"].isEnabled())

    def test_disconnect_discards_ids_but_keeps_selected_names(self):
        self.populate(fixture())
        self.window.set_worker(None)
        self.assertEqual(self.window.signal_dock.signal_checkboxes, {})
        self.assertEqual(self.window.var_dock.var_widgets, {})
        self.assertEqual(self.window.pid_dock.pid_widgets, {})
        self.assertEqual(self.window.setpoint_dock.variable_ids, {})
        self.assertEqual(self.window.signal_dock.saved_names, ["tick"])


if __name__ == "__main__":
    unittest.main()
