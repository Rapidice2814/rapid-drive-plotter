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
    from test_protocol import fixture, debug_fixture, definitions_from


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

    def test_hfi_controls_require_supported_connection_run_and_valid_lock(self):
        from hfi_protocol import HfiInfo
        self.populate(fixture())
        dock = self.window.hfi_dock
        self.assertFalse(dock.apply.isEnabled())
        dock.set_connected(True)
        dock.source.setCurrentIndex(1)
        dock.set_state(FOCState.FOC_STATE_RUN)
        self.assertFalse(dock.apply.isEnabled())
        dock.on_info(HfiInfo(0, 31, 1, 0.04, 0.01, 0))
        self.assertTrue(dock.apply.isEnabled())
        self.assertIn("Encoder", dock.status.text())
        dock.set_state(FOCState.FOC_STATE_STOP)
        self.assertFalse(dock.apply.isEnabled())
        dock.set_supported(False)
        self.assertFalse(dock.read.isEnabled())
        self.assertIn("protocol 9", dock.status.text())
        dock.set_connected(False)
        self.assertIsNone(dock.info)
        self.assertFalse(dock.timer.isActive())

    def test_basics_controls_scroll_inside_small_dock(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QGroupBox

        dock = self.window.basics_dock
        dock.setFloating(True)
        dock.show()
        scroll = dock.scroll_area
        panel = scroll.widget()
        self.assertIsNotNone(panel)
        for width, height in ((560, 780), (420, 420)):
            with self.subTest(width=width, height=height):
                dock.resize(width, height)
                self.app.processEvents()
                self.assertLessEqual(dock.height(), height)
                if height == 420:
                    self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                groups = [panel.layout().itemAt(index).widget()
                          for index in range(panel.layout().count())]
                groups = [group for group in groups if isinstance(group, QGroupBox)]
                for upper, lower in zip(groups, groups[1:]):
                    self.assertLess(upper.geometry().bottom(), lower.geometry().top())
                scroll.ensureWidgetVisible(dock.enter_bootloader_button)
                self.app.processEvents()
                position = dock.enter_bootloader_button.mapTo(scroll.viewport(), QPoint(0, 0))
                self.assertGreaterEqual(position.y(), 0)
                self.assertLessEqual(position.y() + dock.enter_bootloader_button.height(),
                                     scroll.viewport().height())

    def test_firmware_version_refresh_uses_device_info(self):
        from protocol_codec import Packet
        self.populate(fixture())
        self.window._handle_transport_ready(7999.0)
        self.assertIn(MsgType.MSG_GET_DEVICE_INFO, [packet.msg_type for packet in self.sent])
        self.assertTrue(self.window.basics_dock.version_reply_timeout_timer.isActive())
        definitions = definitions_from(fixture())
        counts = tuple(map(len, definitions.lists()))
        info = DeviceInfo(PROTOCOL_VERSION, (0, 3, 1), counts, 6, 8, 7999, 123, 1019, definitions.fingerprint())
        self.window.on_reply(Packet(MsgType.MSG_DEVICE_INFO_REPLY, info))
        self.assertEqual(self.window.basics_dock.version_label.text(), "0.3.1")
        self.assertFalse(self.window.basics_dock.version_reply_timeout_timer.isActive())

    def test_populates_all_lists_and_resolves_reordered_setpoints(self):
        data = fixture()
        data["variables"].reverse()
        data["pids"].reverse()
        definitions = self.populate(data)
        self.assertEqual(len(self.window.signal_dock.signal_checkboxes), 45)
        self.assertEqual(len(self.window.pid_dock.pid_widgets), 4)
        self.assertEqual(len(self.window.var_dock.var_widgets), 0)
        self.assertEqual(self.window.signal_dock.mask_bytes, 6)
        self.assertFalse(self.window.signal_dock.read_mask_button.isEnabled())
        self.window._handle_transport_ready(7999.0)
        dock = self.window.setpoint_dock
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        self.sent.clear()
        dock._activate_mode("speed")
        self.assertIn(MsgType.MSG_GET_TARGETS, [packet.msg_type for packet in self.sent])
        dock.value_widgets["speed_setpoint"].setValue(12.5)
        dock._send_setpoint("speed_setpoint")
        import struct
        self.assertEqual(self.sent[-1].msg_type, MsgType.MSG_SET_TARGET)
        self.assertEqual(struct.unpack("<Bf", self.sent[-1].data), (2, 12.5))

    def test_production_targets_do_not_depend_on_debug_variables(self):
        data = fixture()
        data["variables"] = [entry for entry in data["variables"] if entry["name"] != "speed_setpoint"]
        self.populate(data)
        self.window._handle_transport_ready(7999.0)
        dock = self.window.setpoint_dock
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        self.assertTrue(dock.mode_buttons["speed"].isEnabled())
        self.assertTrue(dock.mode_buttons["position"].isEnabled())

    def test_disconnect_discards_ids_but_keeps_selected_names(self):
        self.populate(fixture())
        self.window.set_worker(None)
        self.assertEqual(self.window.signal_dock.signal_checkboxes, {})
        self.assertEqual(self.window.var_dock.var_widgets, {})
        self.assertEqual(self.window.pid_dock.pid_widgets, {})
        self.assertEqual(self.window.setpoint_dock.variable_ids, {})
        self.assertEqual(self.window.signal_dock.saved_names, ["tick"])

    def prepare_busy_commands(self):
        self.populate(debug_fixture())
        self.window._handle_transport_ready(7999.0)
        self.callbacks = []

        def sender(packet, completed=None):
            self.sent.append(packet)
            if completed is not None:
                self.callbacks.append(completed)

        self.window.set_command_sender(sender, supports_completion=True)
        self.sent.clear()

    def test_regular_replies_update_controls_without_terminal_noise(self):
        from protocol_codec import Packet, PIDPayload, VarPayload, TextPayload
        self.prepare_busy_commands()
        terminal = self.window.command_dock.command_log
        terminal.clear()
        for packet in (Packet(MsgType.MSG_ACK, b""),
                       Packet(MsgType.MSG_PID_REPLY, PIDPayload(0, 1.0, 2.0, 3.0)),
                       Packet(MsgType.MSG_VAR_REPLY, VarPayload(0, 4.0))):
            self.window.on_reply(packet)
        self.assertEqual(terminal.toPlainText(), "")
        self.assertEqual(self.window.pid_dock.pid_widgets[0]["kp"].value(), 1.0)
        self.assertEqual(self.window.var_dock.var_widgets[0]["value"].value(), 4.0)
        self.window.on_reply(Packet(MsgType.MSG_TEXT_REPLY, TextPayload("Warning: example fault")))
        self.assertIn("Warning: example fault", terminal.toPlainText())

    def test_clicked_button_stays_busy_until_reply_and_cannot_repeat(self):
        self.prepare_busy_commands()
        button = self.window.basics_dock.state_button
        text = button.text()
        button.click()
        self.assertTrue(button.is_busy)
        self.assertIn("Working", button.text())
        self.assertFalse(button.isEnabled())
        button.click()
        self.assertEqual(len(self.callbacks), 1)
        self.window.basics_dock.set_state(FOCState.FOC_STATE_IDLE)
        self.assertFalse(button.isEnabled())
        self.callbacks[0](True)
        self.assertFalse(button.is_busy)
        self.assertEqual(button.text(), text)
        self.assertTrue(button.isEnabled())

    def test_batch_button_waits_for_all_replies(self):
        self.prepare_busy_commands()
        dock = self.window.pid_dock
        dock.set_driver_state(FOCState.FOC_STATE_RUN)
        button = dock.pid_refresh_btn
        button.click()
        self.assertEqual(len(self.callbacks), 4)
        for callback in self.callbacks[:-1]:
            callback(True)
            self.assertTrue(button.is_busy)
        self.callbacks[-1](True)
        self.assertFalse(button.is_busy)
        self.assertTrue(button.isEnabled())

    def test_polling_does_not_mark_a_button_busy(self):
        self.prepare_busy_commands()
        self.window.basics_dock.request_state()
        self.assertFalse(self.callbacks)
        self.assertFalse(self.window.basics_dock.state_button.is_busy)

    def test_completion_preserves_new_state_restrictions(self):
        self.prepare_busy_commands()
        dock = self.window.basics_dock
        dock.set_state(FOCState.FOC_STATE_IDLE)
        button = dock.flash_save_button
        button.click()
        self.assertTrue(button.is_busy)
        dock.set_state(FOCState.FOC_STATE_RUN)
        self.callbacks[0](True)
        self.assertFalse(button.is_busy)
        self.assertFalse(button.isEnabled())

    def test_flash_and_bootloader_buttons_available_in_error(self):
        self.prepare_busy_commands()
        dock = self.window.basics_dock
        buttons = (dock.flash_save_button, dock.flash_load_button, dock.flash_clear_button,
                   dock.enter_bootloader_button)
        for state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR):
            dock.set_state(state)
            self.assertTrue(all(button.isEnabled() for button in buttons))
        for button, command in zip(buttons, (MsgType.MSG_FLASH_SAVE, MsgType.MSG_FLASH_LOAD,
                                            MsgType.MSG_FLASH_CLEAR, MsgType.MSG_ENTER_BOOTLOADER)):
            button.click()
            self.assertEqual(self.sent[-1].msg_type, command)
            self.assertTrue(button.is_busy)
            self.callbacks[-1](True)
            self.assertTrue(button.isEnabled())
        for state in (FOCState.FOC_STATE_RUN, FOCState.FOC_STATE_STOP, FOCState.FOC_STATE_FLASH_SAVE):
            dock.set_state(state)
            self.assertFalse(any(button.isEnabled() for button in buttons))
        self.window.set_worker(None)
        self.assertFalse(any(button.isEnabled() for button in buttons))

    def test_worker_completion_is_delivered_on_gui_thread(self):
        import threading
        self.prepare_busy_commands()
        button = self.window.basics_dock.state_button
        button.click()
        thread = threading.Thread(target=lambda: self.callbacks[0](False))
        thread.start()
        thread.join()
        self.assertTrue(button.is_busy)
        self.app.processEvents()
        self.assertFalse(button.is_busy)
        self.assertTrue(button.isEnabled())

    def test_disconnect_and_late_reply_do_not_reenable_buttons(self):
        self.prepare_busy_commands()
        button = self.window.basics_dock.state_button
        button.click()
        self.window.set_worker(None)
        self.assertFalse(button.is_busy)
        self.assertFalse(button.isEnabled())
        self.callbacks[0](False)
        self.assertFalse(button.isEnabled())

    def test_logging_button_keeps_updated_label_on_completion(self):
        self.prepare_busy_commands()
        button = self.window.plot_panel.start_stop_button
        button.click()
        self.assertTrue(button.is_busy)
        self.window._handle_logging_confirmed(False)
        self.assertIn("Start", button.text())
        self.callbacks[0](True)
        self.assertEqual(button.text(), "Start")

    def test_connection_button_stays_busy_until_transport_ready(self):
        self.window.set_worker(None)
        self.window.set_start_connection_callback(lambda endpoint: self.window.set_worker(object()))
        self.window._on_connect_clicked("test-port")
        button = self.window.connect_dock.connect_btn
        self.assertTrue(button.is_busy)
        self.assertFalse(button.isEnabled())
        self.window._handle_transport_ready(7999.0)
        self.assertFalse(button.is_busy)

    def test_failed_connection_clears_busy_and_keeps_error_visible(self):
        self.window.set_worker(None)

        def fail(endpoint):
            raise OSError("Port unavailable")

        self.window.set_start_connection_callback(fail)
        self.window._on_connect_clicked("test-port")
        button = self.window.connect_dock.connect_btn
        self.assertFalse(button.is_busy)
        self.assertTrue(button.isEnabled())
        self.assertIn("Port unavailable", self.window.command_dock.command_log.toPlainText())

    def test_local_send_failure_clears_busy_and_is_visible(self):
        self.prepare_busy_commands()

        def fail(packet, completed=None):
            raise ValueError("Invalid command value")

        self.window.set_command_sender(fail, supports_completion=True)
        button = self.window.basics_dock.state_button
        button.click()
        self.assertFalse(button.is_busy)
        self.assertTrue(button.isEnabled())
        self.assertIn("Invalid command value", self.window.command_dock.command_log.toPlainText())

    def test_non_button_send_failure_is_visible(self):
        self.prepare_busy_commands()

        def fail(packet, completed=None):
            raise ValueError("Invalid background command")

        self.window.set_command_sender(fail, supports_completion=True)
        self.window.basics_dock.request_state()
        self.assertFalse(self.window.basics_dock.state_button.is_busy)
        self.assertIn("Invalid background command", self.window.command_dock.command_log.toPlainText())

    def test_old_completion_cannot_finish_new_button_action(self):
        self.prepare_busy_commands()
        button = self.window.basics_dock.state_button
        button.click()
        old_callback = self.callbacks[0]
        button.reset_busy()
        button.click()
        self.assertTrue(button.is_busy)
        old_callback(False)
        self.assertTrue(button.is_busy)
        self.callbacks[-1](True)
        self.assertFalse(button.is_busy)

    def test_unknown_setpoint_reply_is_ignored(self):
        self.populate(fixture())
        dock = self.window.setpoint_dock
        before = {name: widget.value() for name, widget in dock.value_widgets.items()}
        dock.set_var_value(255, 123.0)
        self.assertEqual(before, {name: widget.value() for name, widget in dock.value_widgets.items()})

    def test_operation_failed_flag_has_a_readable_label(self):
        from protocol_definitions import FOCError
        self.window.basics_dock.set_latched_errors(int(FOCError.FOC_ERROR_OPERATION_FAILED))
        label = self.window.basics_dock.latched_errors_label.text()
        self.assertIn("0x00008000", label)
        self.assertIn("Operation failed", label)
        self.assertNotIn("Unknown", label)

    def test_clear_faults_refreshes_active_latched_and_state(self):
        self.prepare_busy_commands()
        button = self.window.basics_dock.clear_latched_errors_button
        button.click()
        self.assertEqual([packet.msg_type for packet in self.sent], [
            MsgType.MSG_CLEAR_LATCHED_ERRORS, MsgType.MSG_GET_ACTIVE_ERRORS,
            MsgType.MSG_GET_LATCHED_ERRORS, MsgType.MSG_GET_STATE,
        ])
        for callback in self.callbacks[:-1]:
            callback(True)
            self.assertTrue(button.is_busy)
        self.callbacks[-1](True)
        self.assertFalse(button.is_busy)


if __name__ == "__main__":
    unittest.main()
