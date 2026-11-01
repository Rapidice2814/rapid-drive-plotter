"""PID discovery bounds, permissions, fingerprinting and generated controls."""
import unittest
import tempfile
import struct
from test_protocol import fixture, definitions_from, Harness
from test_dynamic_ui import HAS_GUI
from pid_protocol import pack_gain
from device_definitions import parse_rows
from protocol_definitions import FOCState, MsgType
from protocol_codec import Packet, RawPacket, PIDPayload


class PIDMetadataTests(unittest.TestCase):
    def test_pid_metadata_roundtrip_and_bounds_participate_in_fingerprint(self):
        definitions = definitions_from(fixture())
        self.assertEqual(parse_rows(2, definitions.text(2)),
                         [{k: v for k, v in e.items() if k != "default"} for e in definitions.pids])
        data = fixture(); data["pids"][0]["maximum"] = "20"
        self.assertNotEqual(definitions_from(data).fingerprint(), definitions.fingerprint())
        for key, value in (("maximum", "nan"), ("maximum", "1e100"), ("minimum", "-1"), ("states", 0)):
            data = fixture(); data["pids"][0][key] = value
            with self.assertRaises(ValueError): definitions_from(data)
        with self.assertRaises(ValueError): parse_rows(2, "0\tpid_speed\n")
        entry = definitions.pids[0]
        for value in (-1, float("nan"), float("inf"), 1000001):
            with self.assertRaises(ValueError): pack_gain(entry, value)

    def test_correlated_pid_reply_precedes_completion_and_is_cleared_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            h = Harness(directory); h.start()
            events = []
            h.session.send(Packet(MsgType.MSG_GET_PID, PIDPayload(2, None, None, None)),
                           received=lambda raw: events.append(("reply", raw.payload_bytes[0])),
                           completed=lambda success: events.append(("complete", success)))
            h.drain()
            self.assertEqual(events, [("reply", 2), ("complete", True)])
            events.clear()
            h.session.send(Packet(MsgType.MSG_GET_PID, PIDPayload(2, None, None, None)),
                           received=lambda raw: events.append(("reply", raw)),
                           completed=lambda success: events.append(("complete", success)))
            h.session.receive(RawPacket(MsgType.MSG_PID_REPLY, struct.pack("<Bfff", 1, 1, 2, 3)))
            self.assertEqual(events, [("complete", False)])
            self.assertFalse(h.session.receivers)

    def test_pid_storage_and_advertised_bound_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            h = Harness(directory); h.start()
            for packet in (Packet(MsgType.MSG_SET_PID, PIDPayload(2, 1, 2, 3)),
                           Packet(MsgType.MSG_FLASH_SAVE, None),
                           Packet(MsgType.MSG_SET_PID, PIDPayload(2, 4, 5, 6)),
                           Packet(MsgType.MSG_FLASH_LOAD, None)):
                h.session.send(packet); h.drain()
            self.assertFalse(h.errors)
            self.assertFalse(h.rejections)
            self.assertEqual(h.simulator._pids[2], (1, 2, 3))


@unittest.skipUnless(HAS_GUI, "Install GUI dependencies")
class PIDMetadataUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_pid_controls_follow_discovered_bounds_and_writable_states(self):
        from ui.pid_panel import PidDock
        entry = dict(definitions_from(fixture()).pids[0], maximum="12", states=1 << int(FOCState.FOC_STATE_IDLE))
        dock = PidDock(lambda *args, **kwargs: None)
        self.addCleanup(dock.deleteLater)
        dock.set_definitions([entry]); dock.transport_ready()
        dock.set_driver_state(FOCState.FOC_STATE_IDLE)
        self.assertFalse(dock.pid_send_btn.isEnabled())
        dock.pid_widgets[0]["kp"].setValue(1)
        self.assertTrue(dock.pid_send_btn.isEnabled())
        self.assertEqual(dock.pid_widgets[0]["kp"].maximum(), 12)
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        self.assertFalse(dock.pid_send_btn.isEnabled())
        self.assertTrue(dock.pid_widgets[0]["kp"].isEnabled())
        self.assertTrue(dock.pid_refresh_btn.isEnabled())


@unittest.skipUnless(HAS_GUI, "Install GUI dependencies")
class PIDEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.pid_panel import PidControls
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.h = Harness(self.directory.name)
        self.h.start()
        self.sent = []
        def send(packet, **callbacks):
            self.sent.append(packet)
            self.h.session.send(packet, **callbacks)
        self.controls = PidControls(send)
        self.addCleanup(self.controls.deleteLater)
        self.controls.set_definitions(self.h.session.definitions.pids)
        self.controls.set_connected(True)
        self.controls.set_driver_state(FOCState.FOC_STATE_IDLE)

    def pump(self):
        for _ in range(6):
            self.h.drain()
            self.app.processEvents()

    def test_read_and_manual_apply_share_pid_commands_and_preserve_unedited_gains(self):
        self.h.simulator._pids[2] = (0.8, 7, 0.6)
        self.controls.pid_refresh_btn.click()
        self.pump()
        fields = self.controls.pid_widgets[2]
        self.assertAlmostEqual(fields["kp"].value(), 0.8)
        self.assertEqual(fields["ki"].value(), 7)
        self.assertFalse(self.controls.has_edits())
        fields["kp"].setValue(0.2)
        self.assertTrue(self.controls.pid_send_btn.isEnabled())
        self.h.simulator._pids[2] = (0.8, 9, 0.6)
        self.sent.clear()
        self.controls.pid_send_btn.click()
        self.pump()
        self.assertEqual([p.msg_type for p in self.sent],
                         [MsgType.MSG_GET_PID, MsgType.MSG_SET_PID, MsgType.MSG_GET_PID])
        self.assertEqual(self.sent[1].data.ki, 9)
        self.assertAlmostEqual(self.h.simulator._pids[2][0], 0.2)
        self.assertEqual(fields["ki"].value(), 9)
        self.assertFalse(self.controls.has_edits())
        self.assertFalse(self.controls.pid_send_btn.isEnabled())
        self.assertFalse(self.h.errors)
        self.assertFalse(self.h.rejections)

    def test_read_requires_confirmation_before_discarding_edits_and_snapshot_keeps_them(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        fields = self.controls.pid_widgets[2]
        fields["kp"].setValue(0.2)
        with patch("ui.pid_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.controls.pid_refresh_btn.click()
        self.assertFalse(self.sent)
        snapshots = []
        self.controls.read(completed=lambda success, values, error: snapshots.append((success, values)))
        self.pump()
        self.assertTrue(snapshots[0][0])
        self.assertEqual(fields["kp"].value(), 0.2)
        self.assertTrue(self.controls.has_edits())
        with patch("ui.pid_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.controls.pid_refresh_btn.click()
        self.pump()
        self.assertFalse(self.controls.has_edits())
        self.assertAlmostEqual(fields["kp"].value(), self.h.simulator._pids[2][0])

    def test_state_change_after_merge_read_prevents_write_and_keeps_edits(self):
        for state in (FOCState.FOC_STATE_ERROR, FOCState.FOC_STATE_RUN):
            with self.subTest(state=state):
                self.sent.clear()
                self.controls.set_driver_state(FOCState.FOC_STATE_IDLE)
                self.controls.pid_widgets[2]["kp"].setValue(0.2)
                self.controls.apply()
                self.controls.set_driver_state(state)
                self.pump()
                self.assertEqual([p.msg_type for p in self.sent], [MsgType.MSG_GET_PID])
                self.assertTrue(self.controls.has_edits())
                self.assertFalse(self.controls.busy)
                self.assertTrue(self.controls.pid_refresh_btn.isEnabled())
                self.assertFalse(self.controls.pid_send_btn.isEnabled())

    def test_unread_zero_edit_and_tiny_preset_gain_are_explicit_values(self):
        from PySide6.QtCore import QLocale
        field = self.controls.pid_widgets[2]["kp"]
        field.setLocale(QLocale("nl_NL"))
        field.lineEdit().textEdited.emit("0")
        self.assertIn((2, "kp"), self.controls.dirty)
        self.controls.apply()
        self.pump()
        self.assertEqual(self.h.simulator._pids[2][0], 0)
        self.controls.stage({"pid_speed": {"kp": 1e-15}})
        self.controls.apply()
        self.pump()
        self.assertGreater(self.h.simulator._pids[2][0], 0)
        self.assertAlmostEqual(self.h.simulator._pids[2][0] / 1e-15, 1)
        self.assertNotIn("e", field.text().lower())
        field.setValue(0.125)
        self.assertEqual(field.text(), "0,125")

    def test_disconnect_discards_late_completion_without_write_or_restoring_edits(self):
        self.controls.pid_widgets[2]["kp"].setValue(0.2)
        self.controls.apply()
        self.controls.set_connected(False)
        self.pump()
        self.assertEqual([p.msg_type for p in self.sent], [MsgType.MSG_GET_PID])
        self.assertFalse(self.controls.busy)
        self.assertFalse(self.controls.has_edits())
        self.assertFalse(self.controls.pid_refresh_btn.isEnabled())

    def test_reset_unread_gain_removes_optional_edit_without_sending_a_command(self):
        field = self.controls.pid_widgets[2]["kp"]
        field.setValue(0.2)
        self.assertFalse(self.controls.reset_buttons[(2, "kp")].isHidden())
        self.controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(field.text(), "Not read")
        self.assertNotIn("kp", self.controls.editor_gains()["pid_speed"])
        self.assertFalse(self.controls.modified)
        self.assertFalse(self.controls.has_edits())
        self.assertTrue(self.controls.reset_buttons[(2, "kp")].isHidden())
        self.assertFalse(self.sent)

    def test_controller_reset_restores_driver_baseline_and_clears_unread_gains(self):
        self.controls.read(); self.pump()
        expected = {g: w.value() for g, w in self.controls.pid_widgets[2].items()}
        self.sent.clear()
        for gain, value in (("kp", 0.2), ("ki", 8), ("kd", 0.3)):
            self.controls.pid_widgets[2][gain].setValue(value)
        self.controls.controller_reset_buttons[2].click()
        self.assertEqual({g: w.value() for g, w in self.controls.pid_widgets[2].items()}, expected)
        self.assertFalse(self.controls.modified)
        self.assertTrue(self.controls.controller_reset_buttons[2].isHidden())
        self.controls.set_connected(True)
        self.controls.set_driver_state(FOCState.FOC_STATE_IDLE)
        for gain, value in (("kp", 0.2), ("ki", 8), ("kd", 0.3)):
            self.controls.pid_widgets[2][gain].setValue(value)
        self.controls.controller_reset_buttons[2].click()
        self.assertEqual(self.controls.editor_gains()["pid_speed"], {})
        self.assertTrue(all(w.text() == "Not read" for w in self.controls.pid_widgets[2].values()))
        self.assertFalse(self.sent)
