"""Discovered production settings, atomic updates, and UI safety regressions."""
from dataclasses import replace
import struct
import tempfile
import unittest
from test_protocol import Harness, fixture, debug_fixture, definitions_from
from test_dynamic_ui import HAS_GUI
from configuration_protocol import encode_configuration, decode_configuration, pack_value
from device_definitions import Definitions, DeviceInfo, parse_rows
from protocol_codec import Packet, RawPacket, VarPayload
from protocol_definitions import MsgType, FOCState, ControlMode, PROTOCOL_VERSION


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.h = Harness(self.directory)
        self.h.start(["tick"])

    def send(self, command, payload):
        self.h.session.send(Packet(command, payload))
        self.h.drain()
        self.assertFalse(self.h.errors)

    def test_default_catalog_has_no_duplicate_variables_and_targets_still_work(self):
        self.assertEqual(self.h.session.definitions.variables, [])
        self.assertEqual(self.h.simulator._variables, {})
        from protocol_codec import StatePayload, ControlModePayload
        self.send(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN))
        self.send(MsgType.MSG_SET_CONTROL_MODE, ControlModePayload(ControlMode.CONTROL_MODE_OPENLOOP))
        self.send(MsgType.MSG_SET_TARGET, struct.pack("<Bf", 1, 2))
        self.send(MsgType.MSG_GET_TARGETS, None)
        self.assertFalse(self.h.rejections)
        self.assertEqual(self.h.simulator._targets["dq_current_setpoint.q"], 2)
        self.assertEqual(self.h.simulator._sample_values(0, 0)["dq_current_setpoint.q"], 2)
        self.send(MsgType.MSG_SET_CONTROL_MODE, ControlModePayload(ControlMode.CONTROL_MODE_SPEED))
        self.send(MsgType.MSG_SET_TARGET, struct.pack("<Bf", 2, 10))
        self.assertEqual(self.h.simulator._targets["speed_setpoint"], 10)
        self.send(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_STOP))
        self.assertTrue(all(value == 0 for value in self.h.simulator._targets.values()))

    def test_schema_discovery_roundtrip_cache_and_bounds_in_fingerprint(self):
        self.assertEqual(self.h.session.info.protocol, PROTOCOL_VERSION)
        self.assertEqual(len(self.h.session.info.encode()), 34)
        definitions = self.h.session.definitions
        self.assertEqual(parse_rows(4, definitions.text(4)),
                         [{k: v for k, v in entry.items() if k != "default"} for entry in definitions.configuration])
        again = Harness(self.directory)
        again.start()
        self.assertTrue(again.session.cache_hit)
        data = fixture()
        data["configuration"][0]["maximum"] = "20"
        changed = Harness(self.directory, data)
        changed.start()
        self.assertFalse(changed.session.cache_hit)
        self.assertNotEqual(changed.session.info.fingerprint, self.h.session.info.fingerprint)

    def test_atomic_related_limits_and_rejection_keeps_session_and_values(self):
        entries = self.h.session.definitions.configuration
        before = dict(self.h.simulator._configuration)
        self.send(MsgType.MSG_SET_CONFIGURATION, encode_configuration(entries, 0, {2: 18}))
        self.assertEqual(self.h.simulator._configuration, before)
        self.assertTrue(self.h.rejections)
        self.assertTrue(self.h.session.active)
        self.send(MsgType.MSG_SET_CONFIGURATION, encode_configuration(entries, 0, {2: 18, 3: 12, 1: 6}))
        self.assertEqual(self.h.simulator._configuration[2], 18)
        self.assertEqual(self.h.simulator._configuration[3], 12)
        self.assertFalse(self.h.simulator._variables)

    def test_raw_wire_nan_duplicates_wrong_groups_and_state_are_rejected(self):
        before = dict(self.h.simulator._configuration)
        malformed = [b"\x00\x01\x00" + struct.pack("<f", float("nan")),
                     b"\x00\x02\x00" + struct.pack("<f", 4) + b"\x00" + struct.pack("<f", 3),
                     b"\x00\x01\x07" + struct.pack("<I", 2), b"\xff\x01\x00" + struct.pack("<f", 4)]
        for payload in malformed:
            self.send(MsgType.MSG_SET_CONFIGURATION, payload)
            self.assertEqual(self.h.simulator._configuration, before)
        self.h.simulator._state = FOCState.FOC_STATE_RUN
        self.send(MsgType.MSG_SET_CONFIGURATION, b"\x00\x01\x00" + struct.pack("<f", 4))
        self.assertEqual(self.h.simulator._configuration, before)

    def test_production_blocks_raw_writes_but_allows_validated_settings(self):
        self.h = Harness(self.directory, debug_fixture())
        self.h.start()
        self.h.simulator.info = replace(self.h.simulator.info, debug_writes=False)
        identifier = self.h.simulator.variable_ids["debug.test_value"]
        old = self.h.simulator._variables[identifier]
        self.h.session.send(Packet(MsgType.MSG_SET_VAR, VarPayload(identifier, 123)))
        self.h.drain()
        self.assertEqual(self.h.simulator._variables[identifier], old)
        self.assertIn("disabled", self.h.rejections[-1])
        self.send(MsgType.MSG_SET_CONFIGURATION, encode_configuration(self.h.session.definitions.configuration, 0, {0: 4}))
        self.assertEqual(self.h.simulator._configuration[0], 4)

    def test_configuration_changes_invalidate_compensation_and_motor_coordinates(self):
        self.h.simulator._anti_flags = 3
        self.send(MsgType.MSG_SET_CONFIGURATION, encode_configuration(self.h.session.definitions.configuration, 1, {7: 7}))
        self.assertEqual(self.h.simulator._anti_flags, 0)
        self.assertFalse(self.h.simulator._encoder_flags & 7)


    def test_only_current_protocol_is_accepted(self):
        current = self.h.session.info.encode()
        self.assertEqual(DeviceInfo.decode(current), self.h.session.info)
        for version in range(PROTOCOL_VERSION):
            with self.assertRaisesRegex(ValueError, "Incompatible protocol"):
                DeviceInfo.decode(bytes((version,)) + current[1:])
        for length in (29, 31, 33, 35):
            with self.assertRaises(ValueError):
                DeviceInfo.decode((current + b"\x00")[:length])

    def test_new_groups_and_fields_need_no_plotter_catalog_changes(self):
        data = fixture()
        data["configuration"].append(dict(data["configuration"][0], id=len(data["configuration"]), group_id=9, group="Future group",
                                          name="future.extra", label="New setting", default=1))
        h = Harness(self.directory, data)
        h.start()
        self.assertEqual(h.codec.configuration_list[-1]["name"], "future.extra")
        h.session.send(Packet(MsgType.MSG_SET_CONFIGURATION, encode_configuration(h.codec.configuration_list, 9, {len(data["configuration"]) - 1: 2})))
        h.drain()
        self.assertFalse(h.errors)
        self.assertEqual(h.simulator._configuration[len(data["configuration"]) - 1], 2)

    def test_schema_validation_and_float32_boundary_encoding(self):
        entry = dict(self.h.codec.configuration_list[0], minimum="100000005e-11")
        self.assertEqual(pack_value(entry, 0.001), struct.pack("<f", 0.001))
        for field, value in (("maximum", "nan"), ("states", 0), ("group_id", 256), ("type", "unknown")):
            data = fixture()
            data["configuration"][0][field] = value
            with self.assertRaises(ValueError):
                definitions_from(data)
        with self.assertRaises(ValueError):
            decode_configuration(self.h.codec.configuration_list, b"\x00\x00")

    def test_wrong_group_readback_is_not_accepted(self):
        self.h.session.send(Packet(MsgType.MSG_GET_CONFIGURATION, b"\x00"))
        entries = self.h.codec.configuration_list
        wrong = encode_configuration(entries, 1, {e["id"]: self.h.simulator._configuration[e["id"]] for e in entries if e["group_id"] == 1})
        self.h.session.receive(RawPacket(MsgType.MSG_CONFIGURATION_REPLY, wrong))
        self.assertTrue(self.h.errors)


@unittest.skipUnless(HAS_GUI, "Install GUI dependencies")
class ConfigurationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_global_controls_apply_one_atomic_group_and_verify(self):
        from ui.configuration_panel import ConfigurationDock
        with tempfile.TemporaryDirectory() as directory:
            h = Harness(directory); h.start()
            dock = ConfigurationDock(h.session.send)
            self.addCleanup(dock.deleteLater)
            dock.set_definitions(h.session.definitions.configuration)
            dock.set_connected(True)
            dock.set_state(FOCState.FOC_STATE_IDLE)
            def pump():
                for _ in range(6): h.drain(); self.app.processEvents()
            pump()
            dock.widgets[2].setText("26"); dock._edited(2)
            dock.widgets[3].setText("18"); dock._edited(3)
            self.assertTrue(dock.apply_driver_button.isEnabled())
            dock.apply_to_driver()
            self.assertFalse(dock.apply_driver_button.isEnabled())
            pump()
            self.assertFalse(dock.has_unapplied_changes())
            self.assertEqual(dock.values[2], 26)
            self.assertEqual(dock.values[3], 18)
            self.assertEqual(dock.dirty, {2, 3})  # Still differs from the read baseline.
            dock.set_state(FOCState.FOC_STATE_RUN)
            self.assertTrue(dock.widgets[2].isEnabled())
            dock.set_connected(False)
            self.assertFalse(dock.values)
            self.assertFalse(dock.read_driver_button.isEnabled())
            self.assertFalse(h.errors)
            self.assertFalse(h.rejections)
