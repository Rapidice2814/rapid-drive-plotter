"""Command completion notifications for queued UI actions and cancellation."""
import tempfile
import unittest

from test_protocol import Harness, error_reply
from protocol_codec import Packet, RawPacket
from protocol_definitions import MsgType


class CommandCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.h = Harness(temporary.name)
        self.h.start(["tick"])
        self.finished = []

    def test_queued_commands_complete_individually_on_their_replies(self):
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None),
                            completed=lambda ok: self.finished.append(("state", ok)))
        self.h.session.send(Packet(MsgType.MSG_CLEAR_LATCHED_ERRORS, None),
                            completed=lambda ok: self.finished.append(("clear", ok)))
        self.assertFalse(self.finished)
        self.h.session.receive(RawPacket(MsgType.MSG_TEXT_REPLY, b"warning: diagnostic"))
        self.assertFalse(self.finished)
        self.h.session.receive(RawPacket(MsgType.MSG_STATE_REPLY, b"\x0e"))
        self.assertEqual(self.finished, [("state", True)])
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        self.assertEqual(self.finished, [("state", True), ("clear", True)])
        self.assertFalse(self.h.session.completions)

    def test_rejected_command_completes_without_disconnecting(self):
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None), completed=self.finished.append)
        self.h.session.receive(error_reply(MsgType.MSG_GET_STATE))
        self.assertEqual(self.finished, [False])
        self.assertTrue(self.h.session.active)

    def test_stop_completes_pending_and_queued_once(self):
        for command in (MsgType.MSG_GET_STATE, MsgType.MSG_CLEAR_LATCHED_ERRORS):
            self.h.session.send(Packet(command, None), completed=self.finished.append)
        self.h.session.stop()
        self.h.session.stop()
        self.assertEqual(self.finished, [False, False])
        self.assertFalse(self.h.session.completions)

    def test_timeout_completes_pending_and_queued(self):
        for command in (MsgType.MSG_GET_STATE, MsgType.MSG_CLEAR_LATCHED_ERRORS):
            self.h.session.send(Packet(command, None), completed=self.finished.append)
        self.h.session.clock = lambda: self.h.session.deadline + 1
        self.h.session.tick()
        self.assertEqual(self.finished, [False, False])
        self.assertFalse(self.h.session.active)

    def test_mask_waits_for_readback_instead_of_ack(self):
        mask = b"\x01" + bytes(5)
        self.h.session.send(Packet(MsgType.MSG_SET_MASK, mask), completed=self.finished.append)
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        self.assertFalse(self.finished)
        self.h.session.receive(RawPacket(MsgType.MSG_MASK_REPLY, mask))
        self.assertEqual(self.finished, [True])

    def test_rejected_mask_cancels_followup_callbacks(self):
        self.h.session.send(Packet(MsgType.MSG_SET_MASK, bytes(6)), completed=self.finished.append)
        self.h.session.send(Packet(MsgType.MSG_START_LOG, None), completed=self.finished.append)
        self.h.session.receive(error_reply(MsgType.MSG_SET_MASK))
        self.assertEqual(self.finished, [False, False])
        self.assertFalse(self.h.session.commands)
        self.assertFalse(self.h.session.completions)
        self.assertTrue(self.h.session.active)

    def test_inactive_session_releases_action_immediately(self):
        self.h.session.stop()
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None), completed=self.finished.append)
        self.assertEqual(self.finished, [False])


if __name__ == "__main__":
    unittest.main()
