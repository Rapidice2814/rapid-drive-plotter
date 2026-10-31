"""Calibration metadata, safe chunk transfers, local editing and GUI workflows."""
import math
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch
from test_protocol import Harness
from test_dynamic_ui import HAS_GUI
from calibration_protocol import CalibrationInfo, CalibrationTable, CHUNK
from protocol_codec import Packet, RawPacket, StatePayload, VarPayload, PIDPayload
from protocol_definitions import MsgType, FOCState


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.h = Harness(directory.name)
        self.h.start(["tick"])

    def command(self, command, payload=b"", completed=None):
        self.h.session.send(Packet(command, payload), completed=completed)
        self.h.drain()
        self.assertFalse(self.h.errors)

    def upload(self, kind, values, crc=None):
        self.command(MsgType.MSG_BEGIN_CALIBRATION_UPLOAD, struct.pack("<BH", kind, len(values)))
        for offset in range(0, len(values), CHUNK):
            chunk = values[offset:offset + CHUNK]
            self.command(MsgType.MSG_WRITE_CALIBRATION_TABLE, struct.pack("<BHB", kind, offset, len(chunk)) + struct.pack(f"<{len(chunk)}f", *chunk))
        checksum = zlib.crc32(struct.pack(f"<{len(values)}f", *values)) if crc is None else crc
        self.command(MsgType.MSG_COMMIT_CALIBRATION_UPLOAD, struct.pack("<BI", kind, checksum))

    def test_payload_roundtrip_and_malformed_inputs(self):
        info = CalibrationInfo(1, 5, 3, 0, 500, 3, 1000, 2)
        self.assertEqual(CalibrationInfo.decode(info.encode()), info)
        table = CalibrationTable(0, 4, 128, (0.0, 0.01))
        self.assertEqual(CalibrationTable.decode(table.encode()).offset, 4)
        for malformed in (b"", info.encode() + b"x", bytes((2,)) + info.encode()[1:]):
            with self.assertRaises(ValueError):
                CalibrationInfo.decode(malformed)
        for malformed in (b"", table.encode()[:-1], struct.pack("<BHHBf", 0, 0, 128, 1, math.nan)):
            with self.assertRaises(ValueError):
                CalibrationTable.decode(malformed)

    def test_upload_requires_order_complete_crc_and_safe_state(self):
        self.command(MsgType.MSG_BEGIN_CALIBRATION_UPLOAD, struct.pack("<BH", 1, 2000))
        self.command(MsgType.MSG_WRITE_CALIBRATION_TABLE, struct.pack("<BHBf", 1, 1, 1, 0.1))
        self.assertTrue(self.h.rejections)
        self.command(MsgType.MSG_COMMIT_CALIBRATION_UPLOAD, struct.pack("<BI", 1, 0))
        self.assertFalse(self.h.simulator._anti_flags & 1)
        self.command(MsgType.MSG_SET_STATE, bytes((FOCState.FOC_STATE_RUN,)))
        self.assertEqual(self.h.simulator._state, FOCState.FOC_STATE_IDLE)
        self.command(MsgType.MSG_ABORT_CALIBRATION_UPLOAD)
        self.assertIsNone(self.h.simulator._cal_upload)
        self.assertFalse(self.h.simulator._anti_flags & 3)

    def test_encoder_and_anticog_tables_roundtrip_and_enable(self):
        values = [0.005 * math.sin(i * 2 * math.pi / 128) for i in range(128)]
        self.upload(0, values)
        self.assertFalse(self.h.rejections)
        self.assertEqual(self.h.simulator._encoder_flags & 3, 1)
        self.command(MsgType.MSG_ENABLE_CALIBRATION, b"\0\1")
        self.assertEqual(self.h.simulator._encoder_flags & 3, 3)
        values = [0.1] * 2000
        self.upload(1, values)
        self.assertEqual(self.h.simulator._anti_flags, 1)
        self.command(MsgType.MSG_ENABLE_CALIBRATION, b"\1\1")
        self.assertEqual(self.h.simulator._anti_flags, 3)
        self.command(MsgType.MSG_ENABLE_CALIBRATION, b"\0\0")
        self.assertFalse(self.h.simulator._anti_flags & 1)

    def test_bad_crc_and_nonmonotonic_encoder_do_not_replace_accepted_table(self):
        before = list(self.h.simulator._cal_tables[0])
        values = [0.0] * 128
        self.upload(0, values, crc=0)
        self.assertTrue(self.h.rejections)
        self.command(MsgType.MSG_ABORT_CALIBRATION_UPLOAD)
        values[64] = 0.09
        self.upload(0, values)
        self.assertEqual(self.h.simulator._cal_tables[0], before)
        self.assertIsNotNone(self.h.simulator._cal_upload)

    def test_multirevolution_start_progress_cancel_and_complete(self):
        self.command(MsgType.MSG_START_CALIBRATION, b"\1\5")
        self.assertEqual(self.h.simulator._cal_rounds[1], 5)
        self.command(MsgType.MSG_GET_CALIBRATION_INFO, b"\1")
        self.assertEqual(self.h.simulator._anti_stage, 3)
        self.command(MsgType.MSG_SET_STATE, bytes((FOCState.FOC_STATE_STOP,)))
        self.assertEqual(self.h.simulator._anti_stage, 8)
        self.assertFalse(self.h.simulator._anti_flags & 1)
        self.h.simulator._state = FOCState.FOC_STATE_IDLE
        self.command(MsgType.MSG_START_CALIBRATION, b"\1\2")
        self.h.simulator._anti_started -= 3
        self.command(MsgType.MSG_GET_CALIBRATION_INFO, b"\1")
        self.assertEqual(self.h.simulator._anti_progress, 1000)
        self.assertEqual(self.h.simulator._anti_stage, 6)
        self.assertEqual(self.h.simulator._anti_flags, 1)

    def test_reply_range_mismatch_ends_session(self):
        self.h.session.send(Packet(MsgType.MSG_GET_CALIBRATION_TABLE, struct.pack("<BHB", 0, 0, 1)))
        self.h.session.receive(RawPacket(MsgType.MSG_CALIBRATION_TABLE_REPLY, CalibrationTable(0, 1, 128, (0.0,)).encode()))
        self.assertTrue(self.h.errors)


@unittest.skipUnless(HAS_GUI, "Qt dependencies required")
class CalibrationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from plot_window import PlotWindow
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.h = Harness(directory.name)
        self.h.start(["tick"])
        self.window = PlotWindow()
        self.window.set_command_sender(self.h.session.send, supports_completion=True)
        self.window.set_worker(self.h.simulator)
        self.dock = self.window.calibration_dock
        self.window.show()
        self.dock.raise_()
        self.h.simulator.data_callback = self.receive
        self.window._handle_transport_ready(8000)
        self.pump()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window.set_worker(None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def receive(self, buffer):
        for raw in self.h.codec.extract_packets(buffer):
            self.h.session.receive(raw)
            decoded = self.h.codec.decode_packet(raw)
            if decoded is not None:
                self.window.enqueue_reply(decoded)

    def pump(self):
        for _ in range(600):
            self.h.drain()
            self.app.processEvents()
            if self.h.commands.empty() and self.dock.transfer is None:
                return
        self.fail("GUI transfer did not finish")

    def test_routine_result_readback_and_fault_reason_stay_visible(self):
        from protocol_codec import TextPayload
        self.dock.read_routine_result()
        self.pump()
        self.assertIn("unavailable in simulation", self.dock.routine_result.text())
        reason = "Routine result: Encoder calibration failed. Rotor tracking error exceeds limit; stage=3."
        self.window.on_reply(Packet(MsgType.MSG_ROUTINE_RESULT_REPLY, TextPayload(reason)))
        self.dock.set_state(FOCState.FOC_STATE_ERROR)
        self.assertEqual(self.dock.routine_result.text(), reason)

    def test_new_tab_contains_encoder_controls_and_local_editors(self):
        self.assertIn(self.dock, self.window.tabifiedDockWidgets(self.window.basics_dock))
        self.assertTrue(self.dock.isAncestorOf(self.window.basics_dock.encoder_source_combo))
        self.dock.download(0)
        self.pump()
        editor = self.dock.editors[0]
        self.assertEqual(editor.table.rowCount(), 128)
        self.assertEqual(len(editor.curves[0].getData()[0]), 129)
        editor.table.item(0, 2).setText("0.001")
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.values[0], 0.001)
        editor.table.item(0, 2).setText("nan")
        self.assertEqual(editor.values[0], 0.001)
        editor.select_point(1, 0)
        editor.radial_handle.setPos(*editor._radial_point(1, 0.002))
        editor._radial_edited()
        self.assertEqual(editor.values[1], 0.002)
        self.assertEqual(self.h.simulator._cal_tables[0][1], 0)

    def test_progress_time_estimate_is_local_stall_aware_and_frozen_at_completion(self):
        def info(progress, flags=5, stage=3):
            return CalibrationInfo(1, flags, stage, 0, progress, 1, 1000, 2)
        sent = []
        self.dock.send = lambda packet, **kwargs: sent.append(packet)
        self.dock.timing = [None, None]
        with patch("ui.calibration_panel.time.monotonic", return_value=100):
            self.dock.on_info(info(0))
        self.assertIn("Estimating", self.dock.time_labels[1].text())
        with patch("ui.calibration_panel.time.monotonic", return_value=110):
            self.dock.on_info(info(100))
        self.assertIn("~1m 30s", self.dock.time_labels[1].text())
        with patch("ui.calibration_panel.time.monotonic", return_value=122):
            self.dock.on_info(info(100))
        self.assertIn("stalled", self.dock.time_labels[1].text())
        with patch("ui.calibration_panel.time.monotonic", return_value=200):
            self.dock.on_info(info(1000, 1, 6))
        finished = self.dock.time_labels[1].text()
        with patch("ui.calibration_panel.time.monotonic", return_value=300):
            self.dock.on_info(info(1000, 1, 6))
        self.assertEqual(self.dock.time_labels[1].text(), finished)
        with patch("ui.calibration_panel.time.monotonic", return_value=400):
            self.dock.on_info(info(0))
        self.assertIn("0m 00s", self.dock.time_labels[1].text())
        self.assertEqual(sent, [])  # No driver commands for ETA calculation.

    def test_dedicated_anticog_pid_controls_use_discovered_id_and_readback(self):
        self.dock.set_definitions([], [{"id": 92, "name": "pid_anticogging"}])
        self.dock.set_errors(active=0, latched=0)
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.assertEqual([field.value() for field in self.dock.anticog_gain_inputs], [10, 20, 0.2])
        sent = []
        self.dock.send = lambda packet, **kwargs: sent.append(packet)
        self.dock.anticog_gain_inputs[0].setValue(3)
        self.dock.apply_anticog_pid()
        self.assertEqual(sent[-1].msg_type, MsgType.MSG_SET_PID)
        self.assertEqual(sent[-1].data.controller_id, 92)
        self.assertEqual(sent[-1].data.kp, 3)
        self.dock.on_setup_pid(PIDPayload(92, 4, 5, 0.1))
        self.assertEqual([field.value() for field in self.dock.anticog_gain_inputs], [4, 5, 0.1])
        self.dock.set_state(FOCState.FOC_STATE_ANTICOGGING)
        self.assertFalse(self.dock.anticog_pid_apply.isEnabled())

    def test_encoder_calibration_d_current_is_discovered_editable_and_read_back(self):
        self.window._handle_definitions((self.h.session.definitions, self.h.session.info,
                                        bytes(self.h.session.info.mask_bytes), self.h.simulator))
        entry = self.dock.calibration_current_entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry["name"], "encoder.calibration_current")
        self.assertEqual(entry["unit"], "A")
        self.dock.read_calibration_current()
        self.pump()
        self.assertAlmostEqual(self.dock.calibration_current.value(), 0.25)
        self.dock.calibration_current.setValue(0.6)
        self.dock.apply_calibration_current()
        self.pump()
        self.assertAlmostEqual(self.h.simulator._configuration[entry["id"]], 0.6, places=5)
        self.assertAlmostEqual(self.dock.calibration_current.value(), 0.6)
        self.dock.set_state(FOCState.FOC_STATE_ENCODER_CALIBRATION)
        self.assertFalse(self.dock.calibration_current_apply.isEnabled())

    def test_angles_are_displayed_in_radians(self):
        encoder = self.dock.editors[0]
        encoder.set_values([0, 0.01, 0, -0.01])
        self.assertEqual(encoder.table.horizontalHeaderItem(1).text(), "Angle (rad)")
        self.assertAlmostEqual(float(encoder.table.item(1, 1).text()), math.pi / 2, places=5)
        self.assertIn("rad", encoder.summary.text())
        self.assertNotIn("°", encoder.summary.text())
        self.assertIn("0.02 rad", encoder.summary.text())

    def test_radial_geometry_shared_scale_and_encoder_sign(self):
        encoder, anti = self.dock.editors
        encoder.set_values([0, -0.01, 0, 0.01])
        x, y = encoder.curves[0].getData()
        self.assertAlmostEqual(x[0], 1)
        self.assertAlmostEqual(y[1], 1.25)
        self.assertAlmostEqual(y[3], -0.75)
        self.assertAlmostEqual(x[-1], x[0])
        self.assertAlmostEqual(y[-1], y[0])
        self.assertAlmostEqual(encoder.radial_span, 0.02)
        anti.set_values([0.5, -0.5, 0, 0, 0, 0, 0, 0])
        self.assertEqual(anti.radial_span, 1)
        self.assertAlmostEqual(anti.curves[0].getData()[0][0], 1.25)
        self.assertAlmostEqual(anti.curves[1].getData()[0][0], 0.75)
        anti.table.item(0, 3).setText("-3")
        self.assertEqual(anti.radial_span, 4)
        self.assertAlmostEqual(anti.curves[0].getData()[0][0], 1.0625)
        self.assertAlmostEqual(anti.curves[1].getData()[0][0], 0.625)
        self.assertIn("auto-expanded", anti.summary.text())
        anti.table.item(0, 3).setText("-0.5")
        self.assertEqual(anti.radial_span, 1)
        anti.select_point(1, 1)
        anti.radial_handle.setPos(0.3, 1.25)
        anti._radial_edited()
        self.assertAlmostEqual(anti.values[3], 0.5)
        self.assertAlmostEqual(anti.radial_handle.pos().x(), 0)

    def test_anticog_download_edit_upload_requires_explicit_enable(self):
        from PySide6.QtWidgets import QMessageBox
        self.dock.download(1)
        self.pump()
        editor = self.dock.editors[1]
        self.assertEqual(editor.table.rowCount(), 1000)
        editor.table.item(4, 3).setText("0.2")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.dock.upload(1)
        self.pump()
        self.assertAlmostEqual(self.h.simulator._cal_tables[1][9], 0.2, places=6)
        self.assertEqual(self.h.simulator._anti_flags, 1)
        self.assertFalse(editor.dirty)
        self.assertIsNone(self.dock.transfer)
        self.assertNotIn("error 0", self.dock.status[1].text().lower())
        self.dock.enabled[1].click()
        self.pump()
        self.assertEqual(self.h.simulator._anti_flags, 3)
        self.window.set_worker(None)
        self.assertFalse(editor.values)
        self.assertFalse(self.dock.buttons[1][3].isEnabled())

    def test_transfer_failure_stops_upload_chain(self):
        from PySide6.QtWidgets import QMessageBox
        self.dock.download(0)
        self.pump()
        # Finite but discontinuous: the firmware must reject this at commit.
        self.dock.editors[0].table.item(64, 2).setText("0.09")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.dock.upload(0)
        self.pump()
        self.assertTrue(self.h.rejections)
        self.assertTrue(self.dock.editors[0].dirty)
        self.assertEqual(self.h.simulator._cal_tables[0][64], 0)
        self.assertIsNone(self.dock.transfer)
        self.dock.abort_upload()
        self.pump()
        self.assertIsNone(self.h.simulator._cal_upload)

    def test_save_uses_state_request_without_run_continuation(self):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.dock.save_settings()
        self.pump()
        packets = [self.h.codec.extract_packets(bytearray(frame))[0] for frame in self.h.sent]
        self.assertTrue(any(p.msg_type_int == MsgType.MSG_SET_STATE and p.payload_bytes == bytes((FOCState.FOC_STATE_FLASH_SAVE,)) for p in packets))
        self.assertNotEqual(self.h.simulator._state, FOCState.FOC_STATE_RUN)

    def test_motor_setup_lists_individual_routines_but_not_current_sensors(self):
        expected = {FOCState.FOC_STATE_ALIGNMENT, FOCState.FOC_STATE_ALIGNMENT_TEST,
                    FOCState.FOC_STATE_IDENTIFY, FOCState.FOC_STATE_PID_AUTOTUNE}
        self.assertEqual(set(self.dock.setup_buttons), expected)
        self.assertEqual(self.dock.tabs.tabText(0), "Motor setup")
        self.assertNotIn(FOCState.FOC_STATE_CURRENT_SENSOR_CALIBRATION, self.dock.setup_buttons)
        self.assertNotIn(FOCState.FOC_STATE_CHECKLIST, self.dock.setup_buttons)

    def test_motor_setup_requires_confirmation_and_ack_is_not_completion(self):
        from PySide6.QtWidgets import QMessageBox
        sent = []
        callbacks = []
        def send(packet, *, completed):
            sent.append(packet)
            callbacks.append(completed)
        self.dock.send = send
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.dock.set_errors(active=0, latched=0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.dock.start_setup(FOCState.FOC_STATE_IDENTIFY)
        self.assertFalse(sent)
        for state in self.dock.setup_buttons:
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                self.dock.start_setup(state)
            self.assertEqual(sent[-1], Packet(MsgType.MSG_SET_STATE, StatePayload(int(state))))
            self.assertTrue(self.dock.setup_pending)
            self.assertFalse(any(button.isEnabled() for button in self.dock.setup_buttons.values()))
            self.assertTrue(self.dock.setup_stop.isEnabled())
            callbacks[-1](True)
            self.app.processEvents()
            self.assertIn("not a completion", self.dock.setup_status.text())
            self.assertTrue(self.dock.setup_pending)
            self.dock.set_state(state)
            self.assertIn("Routine running", self.dock.setup_status.text())
            self.dock.set_state(FOCState.FOC_STATE_IDLE)
            self.assertIn("does not confirm success", self.dock.setup_status.text())

    def test_motor_setup_faults_transfers_rejections_and_stop(self):
        from PySide6.QtWidgets import QMessageBox
        button = self.dock.setup_buttons[FOCState.FOC_STATE_IDENTIFY]
        self.dock.set_errors(active=1, latched=0)
        self.assertFalse(button.isEnabled())
        self.assertIn("faults are present", button.toolTip())
        self.dock.set_errors(active=0)
        self.assertTrue(button.isEnabled())
        self.dock.transfer = {"mode": "read"}
        self.dock._permissions()
        self.assertFalse(button.isEnabled())
        self.assertIn("transfer", button.toolTip())
        self.dock.transfer = None
        self.dock._permissions()
        def reject(_packet, *, completed):
            completed(False)
        self.dock.send = reject
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.dock.start_setup(FOCState.FOC_STATE_IDENTIFY)
        self.app.processEvents()
        self.assertFalse(self.dock.setup_pending)
        self.assertIn("rejected or failed", self.dock.setup_status.text())
        self.assertTrue(button.isEnabled())
        sent = []
        self.dock.send = lambda packet, **_kwargs: sent.append(packet)
        self.dock.set_state(FOCState.FOC_STATE_IDENTIFY)
        self.dock.stop_setup()
        self.assertEqual(sent[-1], Packet(MsgType.MSG_SET_STATE, StatePayload(int(FOCState.FOC_STATE_STOP))))
        self.dock.set_connected(False)
        self.assertFalse(self.dock.setup_stop.isEnabled())
        self.assertFalse(button.isEnabled())

    def test_motor_readback_uses_discovered_ids_and_updates_from_replies(self):
        variables = [{"name": name, "id": 100 + i} for i, (name, _title, _unit) in enumerate(self.dock.RESULT_VARIABLES)]
        pids = [{"name": "pid_current_q", "id": 72}, {"name": "pid_current_d", "id": 71}]
        self.dock.set_definitions(variables, pids)
        sent = []
        self.dock.send = lambda packet, **_kwargs: sent.append(packet)
        self.dock.read_setup_values()
        self.assertEqual([p.data.var_id for p in sent if isinstance(p.data, VarPayload)], [100, 101, 102, 103])
        self.assertEqual([p.data.controller_id for p in sent if isinstance(p.data, PIDPayload)], [71, 72])
        self.window.on_reply(Packet(MsgType.MSG_VAR_REPLY, VarPayload(101, 0.25)))
        self.window.on_reply(Packet(MsgType.MSG_PID_REPLY, PIDPayload(72, 1, 2, 0)))
        self.assertIn("0.25 Ω", self.dock.variable_labels[variables[1]["name"]].text())
        self.assertIn("Kp 1, Ki 2", self.dock.pid_labels["pid_current_q"].text())
        # A short PI calculation may finish before the first state poll sees it.
        sent.clear()
        self.dock.setup_pending = True
        self.dock.await_setup_state = True
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.assertFalse(self.dock.setup_pending)
        self.assertEqual(len(sent), 7)
        self.dock.set_definitions([], [])
        self.assertFalse(self.dock.read_setup.isEnabled())
        self.assertTrue(all("Not exposed" in label.text() for label in self.dock.variable_labels.values()))
        self.assertFalse(self.dock.result_pids)

    def test_motor_readback_uses_configuration_when_debug_catalog_is_empty(self):
        definitions = self.h.session.definitions
        self.assertEqual(definitions.variables, [])
        self.dock.set_definitions([], definitions.pids, definitions.configuration)
        self.dock.read_setup_values()
        self.pump()
        self.assertFalse(self.h.errors)
        self.assertFalse(self.dock.result_variables)
        self.assertEqual(len(self.dock.result_configurations), 4)
        self.assertIn("0.2 Ω", self.dock.variable_labels["flash_data.motor.phase_resistance"].text())
        self.assertIn("11", self.dock.variable_labels["flash_data.motor.pole_pairs"].text())
        packets = [self.h.codec.extract_packets(bytearray(frame))[0] for frame in self.h.sent]
        self.assertFalse(any(packet.msg_type_int == MsgType.MSG_GET_VAR for packet in packets))

    def test_checkboxes_send_immediately_and_revert_rejections_without_status_echo(self):
        sent = []
        self.dock.send = lambda packet, completed=None: sent.append((packet, completed))
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        for kind in (0, 1):
            self.dock.on_info(CalibrationInfo(kind, 1, 6, 0, 1000, 1, 128 if kind == 0 else 1000, kind + 1))
            sent.clear()
            self.assertFalse(self.dock.enabled[kind].isChecked())
            self.dock.enabled[kind].click()
            enables = [(p, cb) for p, cb in sent if p.msg_type == MsgType.MSG_ENABLE_CALIBRATION]
            self.assertEqual(len(enables), 1)
            self.assertEqual(enables[0][0].data, bytes((kind, 1)))
            self.assertFalse(self.dock.enabled[kind].isEnabled())
            enables[0][1](False)
            self.assertFalse(self.dock.enabled[kind].isChecked())
            sent.clear()
            self.dock.on_info(CalibrationInfo(kind, 3, 6, 0, 1000, 1, 128 if kind == 0 else 1000, kind + 1))
            self.assertTrue(self.dock.enabled[kind].isChecked())
            self.assertFalse(any(p.msg_type == MsgType.MSG_ENABLE_CALIBRATION for p, _ in sent))

    def test_disabled_calibration_controls_explain_the_blocker(self):
        toggle = self.dock.enabled[1]
        self.assertFalse(toggle.isEnabled())
        self.assertIn("no valid anti-cogging table", toggle.toolTip())
        self.dock.set_state(FOCState.FOC_STATE_RUN)
        self.assertIn("RUN", toggle.toolTip())
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.dock.on_info(CalibrationInfo(1, 9, 0, 0, 0, 3, 1000, 2))
        self.assertIn("unfinished table upload", toggle.toolTip())
        self.dock.on_info(CalibrationInfo(1, 1, 6, 0, 1000, 3, 1000, 2))
        self.assertTrue(toggle.isEnabled())
        self.assertIn("Immediately enable/disable compensation", toggle.toolTip())
        self.dock.transfer = {"mode": "read"}
        self.dock._permissions()
        self.assertIn("transfer is in progress", toggle.toolTip())
        self.dock.set_connected(False)
        self.assertIn("Not connected", toggle.toolTip())

    def test_disabled_checkbox_hover_displays_reason(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication, QToolTip
        self.dock.tabs.setCurrentWidget(self.dock.editors[1].parentWidget())
        self.app.processEvents()
        toggle = self.dock.enabled[1]
        self.dock.widget().ensureWidgetVisible(toggle)
        self.app.processEvents()
        point = toggle.rect().center()
        # Disabled widgets can route help to their parent: resolve the actual hovered control.
        event = QHelpEvent(QEvent.Type.ToolTip, QPoint(0, 0), toggle.mapToGlobal(point))
        # Offscreen Qt may place this window beyond the virtual screen, so supply
        # the hit-test result while still dispatching the real parent help event.
        with patch("ui.disabled_tooltips.QApplication.widgetAt", return_value=toggle):
            QApplication.sendEvent(self.window, event)
        self.assertEqual(QToolTip.text(), toggle.toolTip())
        self.assertIn("no valid anti-cogging table", QToolTip.text())
        QToolTip.hideText()
