"""HFI wire validation, dynamic telemetry, selection guards and UI permissions."""
import math
import struct
import tempfile
import unittest
from test_protocol import Harness
from protocol_codec import Packet, StatePayload, ControlModePayload, RawPacket
from protocol_definitions import MsgType, FOCState, ControlMode
from hfi_protocol import HfiInfo, LAYOUT, SELECTED, VALID
from configuration_protocol import encode_configuration


class HfiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.h = Harness(directory.name)
        self.h.start(["encoder_angle_electrical", "hfi.angle_electrical", "hfi.error_electrical", "hfi.status"])

    def send(self, command, payload=None):
        self.h.session.send(Packet(command, payload))
        self.h.drain()
        self.assertFalse(self.h.errors)

    def enable_and_run(self):
        entries = self.h.session.definitions.configuration
        enabled = next(e for e in entries if e["name"] == "hfi.hfi_enabled")
        self.send(MsgType.MSG_SET_CONFIGURATION, encode_configuration(entries, enabled["group_id"], {enabled["id"]: 1}))
        self.send(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN))
        self.send(MsgType.MSG_SET_CONTROL_MODE, ControlModePayload(ControlMode.CONTROL_MODE_OPENLOOP))

    def test_status_roundtrip_and_invalid_values(self):
        good = LAYOUT.pack(1, 63, 1, 0.04, 0.01, 0)
        self.assertEqual(HfiInfo.decode(good).source, 1)
        self.assertIsInstance(self.h.codec.decode_packet(RawPacket(MsgType.MSG_HFI_INFO_REPLY, good)).data, HfiInfo)
        for bad in (good[:-1], LAYOUT.pack(2, 0, 0, 0, 0, 0), LAYOUT.pack(0, 32, 0, 0, 0, 0),
                    LAYOUT.pack(0, 64, 0, 0, 0, 0), LAYOUT.pack(0, 0, math.nan, 0, 0, 0),
                    LAYOUT.pack(0, 0, 0, -1, 0, 0)):
            with self.assertRaises(ValueError):
                HfiInfo.decode(bad)

    def test_discovered_comparison_signals_and_default_encoder(self):
        self.assertEqual(self.h.simulator._feedback_source, 0)
        self.enable_and_run()
        self.h.simulator._emit_log_packet()
        self.assertEqual(set(self.h.logs[-1].signals),
                         {"encoder_angle_electrical", "hfi.angle_electrical", "hfi.error_electrical", "hfi.status"})
        self.assertEqual(self.h.simulator._feedback_source, 0)
        self.send(MsgType.MSG_GET_HFI_INFO)

    def test_selection_requires_enabled_hfi_run_and_zero_targets(self):
        self.send(MsgType.MSG_SET_FEEDBACK_SOURCE, b"\x01")
        self.assertTrue(self.h.rejections)
        self.enable_and_run()
        self.h.rejections.clear()
        self.send(MsgType.MSG_SET_TARGET, struct.pack("<Bf", 1, 1))
        self.send(MsgType.MSG_SET_FEEDBACK_SOURCE, b"\x01")
        self.assertTrue(self.h.rejections)
        self.send(MsgType.MSG_SET_TARGET, struct.pack("<Bf", 1, 0))
        self.h.rejections.clear()
        self.send(MsgType.MSG_SET_FEEDBACK_SOURCE, b"\x01")
        self.assertFalse(self.h.rejections)
        self.assertEqual(self.h.simulator._feedback_source, 1)
        self.assertTrue(self.h.simulator._hfi_status() & SELECTED)
        self.send(MsgType.MSG_GET_HFI_INFO)
        self.send(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_STOP))
        self.assertEqual(self.h.simulator._feedback_source, 0)
        self.send(MsgType.MSG_GET_HFI_INFO)
        self.assertEqual(self.h.simulator._feedback_source, 0)
        self.assertFalse(self.h.simulator._hfi_status() & VALID)

    def test_bad_payload_rejects_without_disconnecting(self):
        self.send(MsgType.MSG_SET_FEEDBACK_SOURCE, b"")
        self.send(MsgType.MSG_GET_HFI_INFO, b"\x01")
        self.assertEqual(len(self.h.rejections), 2)
        self.assertTrue(self.h.session.active)


if __name__ == "__main__":
    unittest.main()
