"""Encoder source discovery, calibration workflow, and GUI permissions."""
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_protocol import Harness, fixture
from protocol_codec import (Packet, RawPacket, EncoderInfoPayload, EncoderSourceInfo,
                            EncoderSourcePayload, EncoderCorrectionPayload, StatePayload)
from protocol_definitions import MsgType, FOCState, CommandErrorCode


class EncoderProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data = fixture()
        data["encoder_sources"] = [
            {"id": 0, "name": "Timer encoder", "calibratable": True},
            {"id": 7, "name": "Other mechanical sensor", "calibratable": True},
        ]
        self.h = Harness(temporary.name, data)
        self.h.start(["tick"])

    def command(self, command, data=None):
        self.h.session.send(Packet(command, data))
        self.h.drain()
        self.assertFalse(self.h.errors)

    def test_source_names_are_discovered_and_selection_is_guarded(self):
        self.command(MsgType.MSG_GET_ENCODER_INFO)
        driver = self.h.simulator
        payload = EncoderInfoPayload(driver._encoder_source, driver._encoder_flags, 0, 0, 0, driver._encoder_sources)
        self.assertEqual(EncoderInfoPayload.decode(payload.encode()).encode(), payload.encode())
        self.assertEqual([s.source_id for s in payload.sources], [0, 7])
        self.command(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(7))
        self.assertEqual(driver._encoder_source, 7)
        self.assertFalse(driver._encoder_flags & 7)
        self.command(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(99))
        self.assertEqual(driver._encoder_source, 7)
        self.assertIn(CommandErrorCode.UNKNOWN_ID.name, self.h.rejections[-1])
        driver._encoder_flags |= 4  # Stand in for alignment of the new source.
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN))
        self.command(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(0))
        self.assertEqual(driver._encoder_source, 7)

    def test_calibration_simulated_progress_completion_and_save(self):
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_ENCODER_CALIBRATION))
        self.assertEqual(self.h.simulator._state, FOCState.FOC_STATE_ENCODER_CALIBRATION)
        self.h.simulator._encoder_started -= 4
        self.command(MsgType.MSG_GET_ENCODER_INFO)
        self.assertEqual(self.h.simulator._state, FOCState.FOC_STATE_STOP)
        self.assertEqual(self.h.simulator._encoder_stage, 6)
        self.assertEqual(self.h.simulator._encoder_progress, 1000)
        self.assertEqual(self.h.simulator._encoder_flags, 15)
        self.assertEqual(self.h.simulator._saved_encoder[1], 15)

    def test_cancellation_does_not_create_valid_correction(self):
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_ENCODER_CALIBRATION))
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_STOP))
        self.assertEqual(self.h.simulator._encoder_stage, 8)
        self.assertFalse(self.h.simulator._encoder_flags & 1)

    def test_correction_requires_table_and_safe_state(self):
        self.command(MsgType.MSG_SET_ENCODER_CORRECTION, EncoderCorrectionPayload(True))
        self.assertFalse(self.h.simulator._encoder_flags & 2)
        self.assertTrue(self.h.rejections)
        self.h.simulator._encoder_flags = 15
        self.command(MsgType.MSG_SET_ENCODER_CORRECTION, EncoderCorrectionPayload(False))
        self.assertFalse(self.h.simulator._encoder_flags & 2)
        self.command(MsgType.MSG_SET_ENCODER_CORRECTION, EncoderCorrectionPayload(True))
        self.assertTrue(self.h.simulator._encoder_flags & 2)

    def test_malformed_info_is_rejected_by_session(self):
        self.h.session.send(Packet(MsgType.MSG_GET_ENCODER_INFO, None))
        self.h.session.receive(RawPacket(MsgType.MSG_ENCODER_INFO_REPLY, b"bad"))
        self.assertFalse(self.h.session.active)
        self.assertTrue(self.h.errors)

    def test_duplicate_sources_and_trailing_data_are_rejected(self):
        sources = (EncoderSourceInfo(0, "Timer", True), EncoderSourceInfo(0, "Other", True))
        with self.assertRaises(ValueError):
            EncoderInfoPayload(0, 12, 0, 0, 0, sources).encode()
        valid = EncoderInfoPayload(0, 12, 0, 0, 0, sources[:1]).encode()
        for invalid in (valid + b"extra", valid[:-1], struct.pack("<BBBBHB", 0, 128, 0, 0, 0, 1)):
            with self.assertRaises(ValueError):
                EncoderInfoPayload.decode(invalid)


from test_dynamic_ui import HAS_GUI


@unittest.skipUnless(HAS_GUI, "Application dependencies required")
class EncoderUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from plot_window import PlotWindow
        self.window = PlotWindow()
        self.sent = []
        self.window.set_command_sender(self.sent.append)
        self.window.set_worker(object())
        self.window._handle_transport_ready(8000)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window.set_worker(None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_encoder_controls_use_discovered_sources_and_state(self):
        dock = self.window.basics_dock
        dock.set_state(FOCState.FOC_STATE_IDLE)
        info = EncoderInfoPayload(0, 15, 0, 0, 0, (EncoderSourceInfo(0, "Device-supplied name", True), EncoderSourceInfo(7, "Another source", True)))
        self.window.on_reply(Packet(MsgType.MSG_ENCODER_INFO_REPLY, info))
        self.assertEqual(dock.encoder_source_combo.itemText(0), "Device-supplied name")
        self.assertTrue(dock.encoder_calibrate_button.isEnabled())
        dock.encoder_source_combo.setCurrentIndex(1)
        dock.encoder_source_button.click()
        self.assertEqual(self.sent[-2], Packet(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(7)))
        dock.set_state(FOCState.FOC_STATE_ERROR)
        self.assertTrue(dock.encoder_source_button.isEnabled())
        self.assertFalse(dock.encoder_calibrate_button.isEnabled())
        dock.set_state(FOCState.FOC_STATE_RUN)
        self.assertFalse(dock.encoder_source_button.isEnabled())
        self.assertFalse(dock.encoder_correction_button.isEnabled())
        self.window.set_worker(None)
        self.assertEqual(dock.encoder_source_combo.count(), 0)

    def test_calibration_requires_explicit_confirmation(self):
        from PySide6.QtWidgets import QMessageBox
        dock = self.window.basics_dock
        dock.set_state(FOCState.FOC_STATE_IDLE)
        dock.set_encoder_info(EncoderInfoPayload(0, 12, 0, 0, 0, (EncoderSourceInfo(0, "Timer", True),)))
        self.sent.clear()
        with patch("ui.basics_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dock.encoder_calibrate_button.click()
        self.assertFalse(self.sent)
        with patch("ui.basics_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dock.encoder_calibrate_button.click()
        self.assertEqual(self.sent[0], Packet(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_ENCODER_CALIBRATION)))
