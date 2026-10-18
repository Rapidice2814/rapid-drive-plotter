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
from protocol_codec import Packet, RawPacket
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

    def test_new_tab_contains_encoder_controls_and_local_editors(self):
        self.assertIn(self.dock, self.window.tabifiedDockWidgets(self.window.basics_dock))
        self.assertTrue(self.dock.isAncestorOf(self.window.basics_dock.encoder_source_combo))
        self.dock.download(0)
        self.pump()
        editor = self.dock.editors[0]
        self.assertEqual(editor.table.rowCount(), 128)
        self.assertEqual(len(editor.curves[0].getData()[0]), 128)
        editor.table.item(0, 2).setText("0.001")
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.values[0], 0.001)
        editor.table.item(0, 2).setText("nan")
        self.assertEqual(editor.values[0], 0.001)
        editor.select_point(1, 0)
        editor.value_line.setValue(0.002)
        editor._line_edited()
        self.assertEqual(editor.values[1], 0.002)
        self.assertEqual(self.h.simulator._cal_tables[0][1], 0)

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
        self.dock.enabled[1].setChecked(True)
        self.dock._setting(1)
        self.pump()
        self.assertEqual(self.h.simulator._anti_flags, 3)
        self.window.set_worker(None)
        self.assertFalse(editor.values)
        self.assertFalse(self.dock.buttons[1][4].isEnabled())

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

    def test_disabled_calibration_controls_explain_the_blocker(self):
        toggle = self.dock.enabled[1]
        apply = self.dock.buttons[1][2]
        self.assertFalse(toggle.isEnabled())
        self.assertIn("no valid anti-cogging table", toggle.toolTip())
        self.assertEqual(toggle.toolTip(), apply.toolTip())
        self.dock.set_state(FOCState.FOC_STATE_RUN)
        self.assertIn("RUN", apply.toolTip())
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.dock.on_info(CalibrationInfo(1, 9, 0, 0, 0, 3, 1000, 2))
        self.assertIn("unfinished table upload", toggle.toolTip())
        self.dock.on_info(CalibrationInfo(1, 1, 6, 0, 1000, 3, 1000, 2))
        self.assertTrue(apply.isEnabled())
        self.assertIn("Send the selected enable setting", apply.toolTip())
        self.dock.transfer = {"mode": "read"}
        self.dock._permissions()
        self.assertIn("transfer is in progress", apply.toolTip())
        self.dock.set_connected(False)
        self.assertIn("Not connected", apply.toolTip())

    def test_disabled_checkbox_hover_displays_reason(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication, QToolTip
        self.dock.tabs.setCurrentIndex(1)
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
