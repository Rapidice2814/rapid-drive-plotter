"""CAN catalogs are device metadata, including across driver catalog changes."""
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest

from test_protocol import Harness, fixture, definitions_from
from test_dynamic_ui import HAS_GUI
from device_definitions import DeviceInfo, Definitions, DefinitionCache, PAGE_HEADER, parse_rows
from protocol_codec import Packet, RawPacket, CanCyclicTypePayload, CanCyclicRatePayload, ProtocolCodec
from protocol_definitions import MsgType, PROTOCOL_VERSION


class CanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def command(self, harness, packet):
        harness.session.send(packet)
        harness.drain()
        self.assertFalse(harness.errors)

    def test_expanded_catalogs_all_discover_before_telemetry(self):
        data = fixture()
        data["can_cyclic"] = [{"name": f"Additional cyclic message {i:03d}"} for i in range(70)]
        data["signals"].append({"name": "custom.extra_signal", "type": "u32"})
        data["variables"].append({"name": "custom.extra_variable", "type": "f"})
        data["pids"].append(dict(data["pids"][0], name="custom.extra_pid"))
        h = Harness(self.directory, data)
        h.start(["tick"])
        self.assertFalse(h.errors)
        self.assertEqual(h.session.info.counts, (32, 1, len(data["pids"]), 70, len(data["configuration"])))
        self.assertEqual(h.session.definitions.can_cyclic[69], {"id": 69, "name": "Additional cyclic message 069"})
        requests = [h.codec.extract_packets(bytearray(frame))[0] for frame in h.sent]
        pages = [r for r in requests if r.msg_type_int == MsgType.MSG_GET_DEFINITIONS]
        self.assertGreater(sum(r.payload_bytes[0] == 3 for r in pages), 1)
        self.assertLess(max(i for i, r in enumerate(requests) if r.msg_type_int == MsgType.MSG_GET_DEFINITIONS),
                        next(i for i, r in enumerate(requests) if r.msg_type_int == MsgType.MSG_SET_MASK))
        self.command(h, Packet(MsgType.MSG_SET_CAN_CYCLIC_RATE, CanCyclicRatePayload(69, 0xFFFFFFFF)))
        self.command(h, Packet(MsgType.MSG_GET_CAN_CYCLIC_RATE, CanCyclicTypePayload(69)))
        self.assertEqual(h.simulator._can_cyclic_rates[69], 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            h.codec.encode_packet(Packet(MsgType.MSG_GET_CAN_CYCLIC_RATE, CanCyclicTypePayload(70)))

    def test_can_change_invalidates_cache_and_same_new_catalog_uses_cache(self):
        h = Harness(self.directory)
        h.start(["tick"])
        old_fingerprint = h.session.info.fingerprint
        data = fixture()
        data["can_cyclic"].append({"name": "New driver status"})
        newer = Harness(self.directory, data)
        newer.simulator.info = replace(newer.simulator.info, firmware=(9, 2, 0))
        newer.start()
        self.assertFalse(newer.session.cache_hit)
        self.assertNotEqual(newer.session.info.fingerprint, old_fingerprint)
        self.assertEqual(newer.codec.can_cyclic_list[-1]["name"], "New driver status")
        restarted = Harness(self.directory, data)
        restarted.start()
        self.assertTrue(restarted.session.cache_hit)
        self.assertNotIn(MsgType.MSG_GET_DEFINITIONS, [frame[2] for frame in restarted.sent])
        self.assertEqual(restarted.codec.can_cyclic_list, newer.codec.can_cyclic_list)


    def test_can_names_ids_and_order_are_validated_and_fingerprinted(self):
        original = definitions_from(fixture())
        for mutate in (lambda d: d["can_cyclic"].reverse(),
                       lambda d: d["can_cyclic"][0].update(name="Renamed heartbeat")):
            data = fixture()
            mutate(data)
            self.assertNotEqual(definitions_from(data).fingerprint(), original.fingerprint())
        for name in ("", " duplicated ", "bad\tname", "bad\nname", "é", "x" * 128):
            data = fixture()
            data["can_cyclic"][0]["name"] = name
            with self.assertRaises(ValueError):
                definitions_from(data)
        for key in ("name", "id"):
            definitions = definitions_from(fixture())
            definitions.can_cyclic[1][key] = definitions.can_cyclic[0][key]
            with self.assertRaises(ValueError):
                definitions.validate()
        self.assertEqual(parse_rows(3, "0\tBus voltage / current\n"), [{"id": 0, "name": "Bus voltage / current"}])

    def test_empty_can_catalog_and_malformed_info_are_handled(self):
        data = fixture()
        data["can_cyclic"] = []
        h = Harness(self.directory, data)
        h.start(["tick"])
        self.assertFalse(h.errors)
        self.assertEqual(h.session.definitions.can_cyclic, [])
        info = h.session.info
        self.assertEqual(DeviceInfo.decode(info.encode()), info)
        for malformed in (info.encode()[:-2], replace(info, counts=info.counts[:3] + (257, info.counts[4])).encode(),
                          bytes((6,)) + info.encode()[1:], bytes((255,)) + info.encode()[1:]):
            with self.assertRaises(ValueError):
                DeviceInfo.decode(malformed)

    def test_inconsistent_can_page_is_rejected_before_cache_write(self):
        h = Harness(self.directory)
        h.session.start(["tick"])
        h.session.receive(RawPacket(MsgType.MSG_DEVICE_INFO_REPLY, h.simulator.info.encode()))
        h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        for kind in range(3):
            text = h.simulator.definitions.text(kind).encode()
            count = h.simulator.info.counts[kind]
            h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY, PAGE_HEADER.pack(kind, 0, count, count) + text))
        h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY, PAGE_HEADER.pack(3, 0, 9, 1) + b"1\tWrong index\n"))
        self.assertTrue(h.errors)
        self.assertFalse(list(Path(self.directory).glob("definitions-v*.json")))


@unittest.skipUnless(HAS_GUI, "Qt dependencies required")
class CanDiscoveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from plot_window import PlotWindow
        self.window = PlotWindow()
        self.sent = []
        self.window.set_command_sender(self.sent.append)
        self.worker = object()
        self.window.set_worker(self.worker)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window.set_worker(None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def populate(self, data):
        definitions = definitions_from(data)
        info = DeviceInfo(PROTOCOL_VERSION, (1, 0, 0), tuple(map(len, definitions.lists())), 5, 8, 12345, 123, 1019, definitions.fingerprint())
        self.window._handle_definitions((definitions, info, definitions.mask_for_names(["tick"], 8), self.worker))
        self.window._handle_transport_ready(12345)
        self.sent.clear()

    def test_can_controls_are_empty_until_discovery_and_adapt_to_changed_driver(self):
        from PySide6.QtWidgets import QLabel
        dock = self.window.can_dock
        self.assertEqual(dock.cyclic_rate_widgets, {})
        data = fixture()
        data["can_cyclic"] = [{"name": "Completely new cyclic message"}, {"name": "<b>Literal label</b>"}]
        self.populate(data)
        self.assertEqual(set(dock.cyclic_rate_widgets), {0, 1})
        labels = [label.text() for label in dock.findChildren(QLabel)]
        self.assertIn("Completely new cyclic message - current", labels)
        self.assertIn("12345 Hz", dock.cyclic_rate_widgets[0]["input"].toolTip())
        dock.cyclic_rate_widgets[1]["input"].setValue(321)
        dock.cyclic_rate_widgets[1]["set_button"].click()
        self.assertEqual(self.sent[0].data, CanCyclicRatePayload(1, 321))
        self.assertEqual(self.sent[1].data, CanCyclicTypePayload(1))
        self.window.set_worker(None)
        self.assertEqual(dock.cyclic_rate_widgets, {})
        self.window.set_worker(self.worker)
        data["can_cyclic"] = [{"name": "Only new topic"}]
        self.populate(data)
        self.assertEqual(set(dock.cyclic_rate_widgets), {0})
        self.assertEqual(dock.rates_form.rowCount(), 3)
