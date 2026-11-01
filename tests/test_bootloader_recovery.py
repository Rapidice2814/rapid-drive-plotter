"""Bootloader access without protocol discovery, including mismatch recovery."""
import struct
import unittest
from unittest.mock import patch
from test_protocol import Harness
from test_dynamic_ui import HAS_GUI
from bootloader_recovery import request_bootloader
from protocol_codec import ProtocolCodec, RawPacket, CommandErrorPayload
from protocol_definitions import MsgType, FOCState, CommandErrorCode, PROTOCOL_VERSION


class FakeSerial:
    def __init__(self, *, states=None, reject=False, silent=False):
        self.states = list(states or [FOCState.FOC_STATE_STOP, FOCState.FOC_STATE_IDLE])
        self.reject, self.silent = reject, silent
        self.codec = ProtocolCodec(); self.rx = bytearray(); self.requests = []
        self.closed = False; self.now = 0; self.options = None
    def factory(self, **options):
        self.options = options
        return self
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def reset_input_buffer(self): self.rx.clear()
    def write(self, data):
        raw = self.codec.extract_packets(bytearray(data))[0]
        self.requests.append(raw)
        if not self.silent:
            if raw.msg_type_int == MsgType.MSG_GET_STATE:
                state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
                reply = RawPacket(MsgType.MSG_STATE_REPLY, bytes((state,)))
            elif self.reject and raw.msg_type_int == MsgType.MSG_ENTER_BOOTLOADER:
                reply = RawPacket(MsgType.MSG_ERROR, CommandErrorPayload(raw.msg_type_int, CommandErrorCode.INVALID_STATE, "Bootloader denied.").encode())
            else:
                reply = RawPacket(MsgType.MSG_ACK, b"")
            # Old device-info layouts and unrelated text must not trigger discovery.
            self.rx.extend(self.codec.build_packet(RawPacket(MsgType.MSG_DEVICE_INFO_REPLY, bytes((PROTOCOL_VERSION-1,)))))
            self.rx.extend(self.codec.build_packet(reply))
        return len(data)
    def read(self, maximum):
        self.now += 0.01
        chunk = bytes(self.rx[:3]); del self.rx[:3]
        return chunk
    def clock(self): return self.now
    def pause(self, duration): self.now += duration


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, connection, **kwargs):
        return request_bootloader("COM9", serial_factory=connection.factory,
                                  clock=connection.clock, pause=connection.pause, **kwargs)

    def test_recovery_uses_no_version_handshake_and_waits_until_stopped(self):
        port = FakeSerial()
        result = self.run_recovery(port)
        commands = [r.msg_type_int for r in port.requests]
        self.assertEqual(commands, [MsgType.MSG_STOP_LOG, MsgType.MSG_SET_STATE,
                                   MsgType.MSG_GET_STATE, MsgType.MSG_GET_STATE, MsgType.MSG_ENTER_BOOTLOADER])
        self.assertEqual(port.requests[1].payload_bytes, bytes((FOCState.FOC_STATE_STOP,)))
        self.assertNotIn(MsgType.MSG_GET_DEVICE_INFO, commands)
        self.assertTrue(port.closed)
        self.assertIn("accepted", result)
        self.assertEqual(port.options["port"], "COM9")

    def test_error_state_can_enter_bootloader_without_acknowledging_faults(self):
        port = FakeSerial(states=[FOCState.FOC_STATE_ERROR])
        self.run_recovery(port)
        self.assertNotIn(MsgType.MSG_CLEAR_LATCHED_ERRORS, [r.msg_type_int for r in port.requests])
        self.assertEqual(port.requests[-1].msg_type_int, MsgType.MSG_ENTER_BOOTLOADER)

    def test_rejection_timeout_and_motor_not_stopped_are_visible_failures(self):
        port = FakeSerial(reject=True)
        with self.assertRaisesRegex(RuntimeError, "Bootloader denied"):
            self.run_recovery(port)
        self.assertTrue(port.closed)
        port = FakeSerial(silent=True)
        with self.assertRaises(TimeoutError): self.run_recovery(port, timeout=0.1)
        self.assertTrue(port.closed)
        port = FakeSerial(states=[FOCState.FOC_STATE_RUN])
        with self.assertRaisesRegex(RuntimeError, "did not stop"): self.run_recovery(port)
        self.assertNotIn(MsgType.MSG_ENTER_BOOTLOADER, [r.msg_type_int for r in port.requests])


@unittest.skipUnless(HAS_GUI, "Install GUI dependencies")
class RecoveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make_dock(self):
        from ui.connect_panel import SerialConnectDock
        callbacks = []
        with patch("ui.connect_panel.SerialWorker.list_available_ports", return_value=["COM9"]):
            dock = SerialConnectDock(on_bootloader=lambda endpoint, done: callbacks.append((endpoint, done)))
        self.addCleanup(dock.deleteLater)
        return dock, callbacks

    def test_button_is_below_disconnect_and_works_without_a_session(self):
        dock, callbacks = self.make_dock()
        layout = dock.widget().layout()
        self.assertEqual(layout.indexOf(dock.bootloader_btn), layout.indexOf(dock.disconnect_btn) + 1)
        self.assertFalse(dock.connected)
        self.assertTrue(dock.bootloader_btn.isEnabled())
        dock.bootloader_btn.click()
        self.assertEqual(callbacks[0][0], "COM9")
        self.assertTrue(dock.bootloader_btn.is_busy)
        self.assertFalse(dock.connect_btn.isEnabled())
        self.assertFalse(dock.port_combo.isEnabled())
        self.assertFalse(dock.disconnect_btn.isEnabled())
        callbacks[0][1](True, "Bootloader request accepted.")
        self.app.processEvents()
        self.assertFalse(dock.bootloader_btn.is_busy)
        self.assertTrue(dock.connect_btn.isEnabled())
        self.assertIn("accepted", dock.status_label.text())

    def test_mismatched_protocol_still_leaves_recovery_available(self):
        from dataclasses import replace
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            h = Harness(directory)
            h.simulator.info = replace(h.simulator.info, protocol=PROTOCOL_VERSION-1)
            with self.assertRaisesRegex(AssertionError, "Incompatible protocol"):
                h.start()
            self.assertTrue(h.errors)
        dock, callbacks = self.make_dock()
        dock.set_connected(False)
        self.assertTrue(dock.bootloader_btn.isEnabled())
        dock.bootloader_btn.click()
        callbacks[0][1](False, "Port unavailable")
        self.app.processEvents()
        self.assertIn("Port unavailable", dock.status_label.text())
        self.assertTrue(dock.bootloader_btn.isEnabled())
