"""Motor commissioning sequencing, correlation and fail-stop behaviour."""
import struct
import unittest
from unittest.mock import patch
from test_dynamic_ui import HAS_GUI
from setup_protocol import SetupStatus
from protocol_definitions import FOCState, MsgType

class SetupProtocolTests(unittest.TestCase):
    def test_status_roundtrip_and_rejects_malformed_status(self):
        status = SetupStatus(42, 21, 1, 0, 14, 0, 0, 0.5)
        self.assertEqual(SetupStatus.decode(status.encode()), status)
        for bad in (b"", status.encode()[:-1], struct.pack("<IBBBBIIf", 1, 255, 1, 0, 14, 0, 0, 0.5)):
            with self.assertRaises(ValueError): SetupStatus.decode(bad)

@unittest.skipUnless(HAS_GUI, "Qt dependencies required")
class SetupFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QSpinBox
        from ui.setup_panel import SetupPanel
        class Owner:
            def __init__(self):
                self.sent = []; self.rounds = [QSpinBox(), QSpinBox()]
                for field in self.rounds: field.setValue(1)
                self.reads = 0
            def _send(self, packet, callback=None):
                self.sent.append(packet)
                if callback: callback(True)
            def read_setup_values(self): self.reads += 1
            def read_routine_result(self): pass
            def _after_setting(self, success): pass
        self.owner = Owner(); self.panel = SetupPanel(self.owner)
        self.panel.connected = True; self.panel.supported = True
        self.panel.state = FOCState.FOC_STATE_IDLE; self.panel.faults = 0; self.panel.source_valid = True
        from test_protocol import fixture, definitions_from
        entries = definitions_from(fixture()).configuration
        self.panel.configuration.set_definitions(entries)
        self.panel.configuration.values = {e["id"]: e["default"] for e in entries}
        self.panel.permissions()
        self.addCleanup(self.panel.deleteLater)

    def begin(self, indexes):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.begin(indexes)

    def status(self, sequence, routine, status, state=FOCState.FOC_STATE_IDLE, active=0):
        self.panel.on_status(SetupStatus(sequence, int(routine), status, 0, int(state), active, 0, 0.5))

    def requests(self):
        return [p for p in self.owner.sent if p.msg_type in (MsgType.MSG_SET_STATE, MsgType.MSG_START_CALIBRATION)]

    def test_order_defaults_and_fresh_success_required(self):
        self.assertEqual([c.isChecked() for c in self.panel.checks], [True,True,True,True,True,False])
        self.begin([0,1])
        self.assertEqual(self.requests(), [])
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 1)
        self.assertEqual(self.requests()[-1].data.state, FOCState.FOC_STATE_MOTOR_PROBE)
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 1)  # Stale result must not advance.
        self.assertEqual(len(self.requests()), 1)
        self.status(41, FOCState.FOC_STATE_MOTOR_PROBE, 1, FOCState.FOC_STATE_STOP)
        self.assertEqual(len(self.requests()), 1)
        self.status(41, FOCState.FOC_STATE_MOTOR_PROBE, 1)
        self.assertEqual(self.requests()[-1].data.state, FOCState.FOC_STATE_ALIGNMENT)
        self.status(42, FOCState.FOC_STATE_ALIGNMENT, 1)
        self.assertFalse(self.panel.running)
        self.assertIn("completed", self.panel.status.text())

    def test_failure_fault_cancel_and_wrong_result_never_advance(self):
        for mode in range(5):
            self.owner.sent.clear(); self.panel.running = False; self.panel.phase = "idle"
            self.panel.state = FOCState.FOC_STATE_IDLE; self.panel.faults = 0; self.panel.permissions()
            self.begin([0,1,2])
            self.status(10, FOCState.FOC_STATE_PID_AUTOTUNE, 1)
            if mode == 0: self.status(11, FOCState.FOC_STATE_MOTOR_PROBE, 2)
            if mode == 1: self.status(11, FOCState.FOC_STATE_MOTOR_PROBE, 0, active=16)
            if mode == 2: self.status(11, FOCState.FOC_STATE_MOTOR_PROBE, 3)
            if mode == 3: self.status(12, FOCState.FOC_STATE_MOTOR_PROBE, 1)
            if mode == 4: self.panel.on_status(SetupStatus(11,21,1,7,14,0,0,0.5))
            self.assertFalse(self.panel.running)
            self.assertEqual(self.requests()[-1].data.state, FOCState.FOC_STATE_STOP)
            self.assertFalse(any(p.data.state == FOCState.FOC_STATE_ALIGNMENT for p in self.requests()))

    def test_automatic_current_calibration_waits_for_original_step_sequence(self):
        self.begin([0, 1])
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 1)
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 1,
                    FOCState.FOC_STATE_CURRENT_SENSOR_CALIBRATION)
        self.assertTrue(self.panel.running)
        self.assertEqual(len(self.requests()), 1)
        self.status(41, FOCState.FOC_STATE_MOTOR_PROBE, 0, FOCState.FOC_STATE_MOTOR_PROBE)
        self.assertTrue(self.panel.running)
        self.assertEqual(len(self.requests()), 1)
        self.status(41, FOCState.FOC_STATE_MOTOR_PROBE, 1)
        self.assertEqual(self.requests()[-1].data.state, FOCState.FOC_STATE_ALIGNMENT)

    def test_automatic_current_calibration_failure_aborts_without_starting_next_step(self):
        self.begin([0, 1])
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 1)
        self.status(40, FOCState.FOC_STATE_PID_AUTOTUNE, 2,
                    FOCState.FOC_STATE_ERROR, active=16)
        self.assertFalse(self.panel.running)
        self.assertEqual(self.requests()[-1].data.state, FOCState.FOC_STATE_STOP)
        self.assertFalse(any(p.data.state == FOCState.FOC_STATE_ALIGNMENT for p in self.requests()))

    def test_skipped_steps_and_opt_in_anticogging(self):
        self.begin([4,5])
        self.status(0, FOCState.FOC_STATE_NONE, 0)
        self.assertEqual(self.requests()[-1].data, bytes((0,1)))
        self.status(1, FOCState.FOC_STATE_ENCODER_CALIBRATION, 1)
        self.assertEqual(self.requests()[-1].data, bytes((1,1)))
        self.panel.cancel()
        self.assertFalse(self.panel.running)
        before = len(self.requests())
        self.status(2, FOCState.FOC_STATE_ANTICOGGING, 1)
        self.assertEqual(len(self.requests()), before)

    def test_disconnect_stops_without_resume(self):
        self.begin([0,1])
        self.panel.set_connected(False)
        self.assertFalse(self.panel.running)
        before = len(self.requests())
        self.status(1, FOCState.FOC_STATE_MOTOR_PROBE, 1)
        self.assertEqual(len(self.requests()), before)

@unittest.skipUnless(HAS_GUI, "Qt dependencies required")
class SetupSimulatorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_default_workflow_runs_in_order_and_skips_anticogging(self):
        import tempfile
        from test_protocol import Harness
        from plot_window import PlotWindow
        from PySide6.QtWidgets import QMessageBox
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        h = Harness(directory.name); h.start(["tick"])
        w = PlotWindow(); self.addCleanup(w.deleteLater)
        w.set_command_sender(h.session.send, supports_completion=True); w.set_worker(h.simulator)
        def receive(buffer):
            for raw in h.codec.extract_packets(buffer):
                h.session.receive(raw)
                packet = h.codec.decode_packet(raw)
                if packet is not None: w.enqueue_reply(packet)
        h.simulator.data_callback = receive
        w._handle_transport_ready(8000)
        w._handle_definitions((h.session.definitions,h.session.info,bytes(h.session.info.mask_bytes),h.simulator))
        def pump():
            for _ in range(40): h.drain(); self.app.processEvents()
        pump()
        panel = w.calibration_dock.setup_page
        self.assertTrue(panel.run_all.isEnabled(), panel.status.text())
        with patch.object(QMessageBox,"question",return_value=QMessageBox.StandardButton.Yes):
            panel.begin([i for i,c in enumerate(panel.checks) if c.isChecked()])
        pump()
        for _ in range(30):
            h.simulator._setup_started -= 1
            h.simulator._encoder_started -= 5
            panel.poll(); pump()
            if not panel.running: break
        self.assertFalse(panel.running)
        self.assertTrue(all(label.text() == "Completed" for label in panel.results[:-1]),
                        [label.text() for label in panel.results])
        self.assertEqual(panel.results[-1].text(), "Skipped")
        self.assertFalse(h.errors)
        self.assertEqual(h.simulator._setup_sequence,5)
        self.assertEqual(h.simulator._encoder_flags & 3,1)  # Calibrated, not enabled.
        w.set_worker(None); w.close(); self.app.processEvents()
