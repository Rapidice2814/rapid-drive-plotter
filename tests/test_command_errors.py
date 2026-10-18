"""Self-contained error replies and command attribution regressions."""
import struct
import tempfile
import unittest

from test_protocol import Harness, error_reply
from protocol_codec import CommandErrorPayload, Packet, ProtocolCodec, RawPacket, StatePayload
from protocol_definitions import CommandErrorCode, ERROR_REPLY_TYPES, FOCState, MsgType


class CommandErrorCodecTests(unittest.TestCase):
    def test_error_roundtrip_and_display_for_all_reply_types(self):
        error = CommandErrorPayload(MsgType.MSG_SET_STATE, CommandErrorCode.ENCODER_NOT_ALIGNED,
                                    "Encoder alignment is invalid.")
        self.assertEqual(error.encode()[:3], b"\x15\x07\x00")
        codec = ProtocolCodec()
        for reply in ERROR_REPLY_TYPES:
            with self.subTest(reply=reply):
                decoded = codec.decode_packet(RawPacket(reply, error.encode())).data
                self.assertEqual(decoded, error)
                self.assertIn("MSG_SET_STATE", str(decoded))
                self.assertIn("ENCODER_NOT_ALIGNED", str(decoded))
                self.assertIn(error.message, str(decoded))

    def test_unknown_numeric_code_is_preserved(self):
        error = CommandErrorPayload(0xE0, 65000, "Future driver error.")
        self.assertEqual(CommandErrorPayload.decode(error.encode()), error)
        self.assertIn("0xFDE8", str(error))

    def test_text_limit_is_in_utf8_bytes(self):
        error = CommandErrorPayload(0x15, 7, "é" * 80)
        self.assertEqual(len(error.encode()), 163)
        self.assertEqual(CommandErrorPayload.decode(error.encode()), error)
        with self.assertRaises(ValueError):
            CommandErrorPayload(0x15, 7, "é" * 81).encode()

    def test_malformed_errors_are_not_decoded(self):
        for payload in (b"", b"\x15\x07", b"\x15\x07\0", b"\x15\0\0text",
                        b"\x15\x07\0\xff", b"\x15\x07\0text\0", b"\x15\x07\0 ",
                        b"\x15\x07\0" + b"a" * 161):
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, UnicodeError)):
                    CommandErrorPayload.decode(payload)
                self.assertIsNone(ProtocolCodec().decode_packet(RawPacket(MsgType.MSG_ERROR, payload)).data)


class CommandErrorSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.h = Harness(temporary.name)
        self.h.start(["tick"])

    def test_wrong_command_error_is_fatal_not_attributed_to_pending(self):
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.h.session.receive(error_reply(MsgType.MSG_SET_STATE))
        self.assertFalse(self.h.session.active)
        self.assertIn("does not match", self.h.errors[-1])
        self.assertFalse(self.h.rejections)

    def test_separate_text_does_not_complete_or_explain_command(self):
        self.h.session.send(Packet(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN)))
        self.h.session.receive(RawPacket(MsgType.MSG_TEXT_REPLY, b"Unrelated diagnostic."))
        self.assertIsNotNone(self.h.session.pending)
        self.h.session.receive(error_reply(MsgType.MSG_SET_STATE, CommandErrorCode.ACTIVE_FAULTS,
                                          "Active faults: 0x00000010."))
        self.assertTrue(self.h.session.active)
        self.assertIn("0x00000010", self.h.rejections[-1])
        self.assertNotIn("Unrelated", self.h.rejections[-1])

    def test_flash_rejection_then_read_remains_connected(self):
        self.h.simulator._state = int(FOCState.FOC_STATE_RUN)
        self.h.session.send(Packet(MsgType.MSG_FLASH_SAVE, None))
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.h.drain()
        self.assertTrue(self.h.session.active)
        self.assertFalse(self.h.errors)
        self.assertIsNone(self.h.session.pending)
        self.assertIn("MSG_FLASH_SAVE", self.h.rejections[-1])
        self.assertIn("MUST_BE_IDLE", self.h.rejections[-1])

    def test_simulator_reports_fault_bits_in_run_rejection(self):
        self.h.simulator._active_errors = 0x10
        self.h.session.send(Packet(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN)))
        self.h.drain()
        self.assertTrue(self.h.session.active)
        self.assertIn("ACTIVE_FAULTS", self.h.rejections[-1])
        self.assertIn("0x00000010", self.h.rejections[-1])

    def test_buffered_requests_have_separate_error_replies(self):
        replies = []
        simulator = self.h.simulator
        simulator.data_callback = lambda buffer: replies.extend(simulator.codec.extract_packets(buffer))
        frames = b"".join(simulator.codec.build_packet(RawPacket(command, payload))
                          for command, payload in ((MsgType.MSG_SET_NODE_ID, b"\x20"),
                                                   (MsgType.MSG_GET_VAR, b"\xff"),
                                                   (0xE0, b""),
                                                   (MsgType.MSG_GET_STATE, b"")))
        simulator._handle_command_bytes(frames[:4])
        self.assertFalse(replies)
        simulator._handle_command_bytes(frames[4:])
        errors = [CommandErrorPayload.decode(reply.payload_bytes) for reply in replies[:3]]
        self.assertEqual([error.command for error in errors], [MsgType.MSG_SET_NODE_ID,
                                                               MsgType.MSG_GET_VAR, 0xE0])
        self.assertEqual([error.code for error in errors], [CommandErrorCode.OUT_OF_RANGE,
                                                            CommandErrorCode.UNKNOWN_ID,
                                                            CommandErrorCode.UNKNOWN_COMMAND])
        self.assertEqual(replies[-1].msg_type_int, MsgType.MSG_STATE_REPLY)

    def test_malformed_error_does_not_allow_queued_write(self):
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.h.session.send(Packet(MsgType.MSG_CLEAR_LATCHED_ERRORS, None))
        sent = len(self.h.sent)
        self.h.session.receive(RawPacket(MsgType.MSG_ERROR, struct.pack("<BH", 0x16, 7)))
        self.assertFalse(self.h.session.active)
        self.assertEqual(len(self.h.sent), sent)


if __name__ == "__main__":
    unittest.main()
