"""Wire, discovery, cache, simulator, and firmware-contract regressions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import queue
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "USB_debugger"), str(ROOT)]
from protocol_codec import Packet, ProtocolCodec, RawPacket, VarPayload, PIDPayload, StatePayload, CommandErrorPayload
from protocol_definitions import MsgType, PROTOCOL_VERSION, FOCState, CommandErrorCode
from device_definitions import DeviceInfo, Definitions, DefinitionCache, SelectionStore, PAGE_HEADER, parse_rows
from protocol_session import ProtocolSession
from Serial_Simulator.simulator_worker import SimulatorWorker, SimulatorConfig


def fixture():
    return json.loads((ROOT / "Serial_Simulator/definitions.json").read_text())

def error_reply(command, code=CommandErrorCode.INVALID_STATE, message="Command rejected.",
                response=MsgType.MSG_ERROR):
    return RawPacket(response, CommandErrorPayload(command, code, message).encode())


def definitions_from(data):
    return Definitions(*[[dict(entry, **{"bit" if kind == 0 else "id": index})
                          for index, entry in enumerate(data[name])]
                         for kind, name in enumerate(("signals", "variables", "pids"))]).validate()


class Harness:
    def __init__(self, directory, data=None):
        self.commands, self.sent, self.errors, self.ready, self.logs = queue.Queue(), [], [], [], []
        self.rejections = []
        self.codec = ProtocolCodec()
        path = None
        if data is not None:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(data))
        self.simulator = SimulatorWorker(command_queue=self.commands, data_callback=self.receive,
            config=SimulatorConfig(definitions_path=path))
        self.session = ProtocolSession(self.codec, self.transmit, lambda: self.ready.append(True),
            self.errors.append, cache=DefinitionCache(directory), selections=SelectionStore(directory),
            rejected=self.rejections.append)

    def transmit(self, frame):
        self.sent.append(frame)
        self.commands.put(frame)

    def receive(self, buffer):
        for raw in self.codec.extract_packets(buffer):
            self.session.receive(raw)
            if raw.msg_type_int == MsgType.MSG_LOG_DATA:
                self.logs.append(self.codec.decode_packet(raw).data)

    def drain(self):
        for _ in range(500):
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            self.simulator._handle_command_bytes(command)
        raise AssertionError("Command queue did not terminate")

    def start(self, names=None):
        self.session.start(names)
        self.drain()
        if self.errors:
            raise AssertionError(self.errors)
        if not self.ready:
            raise AssertionError("Session did not become ready")


class CodecTests(unittest.TestCase):
    def test_fragmented_and_concatenated_frames(self):
        codec = ProtocolCodec()
        frame = codec.build_packet(RawPacket(int(MsgType.MSG_DEVICE_INFO_REPLY), b"123"))
        for split in range(1, len(frame)):
            buffer = bytearray(frame[:split])
            self.assertEqual(codec.extract_packets(buffer), [])
            buffer.extend(frame[split:] + frame)
            self.assertEqual(len(codec.extract_packets(buffer)), 2)
            self.assertFalse(buffer)

    def test_oversized_frame_resynchronizes(self):
        codec = ProtocolCodec()
        buffer = bytearray(b"\xaa\x55\x01\xff\xff" + codec.build_packet(RawPacket(1, b"123")))
        self.assertEqual(codec.extract_packets(buffer), [RawPacket(1, b"123")])

    def test_runtime_width_and_confirmed_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            h = Harness(directory)
            h.start(["tick"])
            self.assertEqual(h.codec.mask_bytes, 5)
            before = h.codec.log_mask
            h.codec.encode_packet(Packet(MsgType.MSG_SET_MASK, bytes(5)))
            self.assertEqual(h.codec.log_mask, before)
            with self.assertRaises(ValueError):
                h.codec.set_log_mask(bytes(8))
            with self.assertRaises(ValueError):
                h.codec.set_log_mask(bytes(4) + b"\x80")
            with self.assertRaises(ValueError):
                h.codec.set_log_mask(b"\xff\x01" + bytes(3))

    def test_no_static_definitions(self):
        codec = ProtocolCodec()
        self.assertEqual(codec.signal_list, [])
        self.assertEqual(codec.variable_list, [])
        self.assertEqual(codec.pid_list, [])
        self.assertEqual(codec.mask_bytes, 0)

    def test_command_numbering_and_removed_version_commands(self):
        prefix = [MsgType.MSG_ENTER_BOOTLOADER, MsgType.MSG_GET_DEVICE_INFO,
                  MsgType.MSG_DEVICE_INFO_REPLY, MsgType.MSG_GET_DEFINITIONS,
                  MsgType.MSG_DEFINITIONS_REPLY, MsgType.MSG_DEFINITIONS_ERROR, MsgType.MSG_LOG_DATA]
        self.assertEqual(list(map(int, prefix)), list(range(7)))
        self.assertEqual([int(message) for message in MsgType if message < 0xFA], list(range(0x28)))
        self.assertEqual(int(MsgType.MSG_UNKNOWN_TYPE), 0xFA)
        self.assertEqual(int(MsgType.MSG_ERROR), 0xFF)
        self.assertFalse(hasattr(MsgType, "MSG_GET_VERSION"))
        self.assertFalse(hasattr(MsgType, "MSG_VERSION_REPLY"))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.h = Harness(self.directory)

    def test_paginated_discovery_and_telemetry(self):
        self.h.start(["tick", "adc_values.motor_temp"])
        commands = [frame[2] for frame in self.h.sent]
        self.assertEqual(commands[:2], [MsgType.MSG_GET_DEVICE_INFO, MsgType.MSG_STOP_LOG])
        self.assertGreater(commands.count(MsgType.MSG_GET_DEFINITIONS), 3)
        self.assertEqual(commands[-3:], [MsgType.MSG_SET_MASK, MsgType.MSG_GET_MASK, MsgType.MSG_START_LOG])
        self.h.simulator._emit_log_packet()
        self.assertEqual(set(self.h.logs[-1].signals), {"tick", "adc_values.motor_temp"})
        self.assertEqual(len(self.h.session.definitions.variables), 19)
        self.assertEqual(len(self.h.session.definitions.pids), 4)

    def test_restart_uses_persisted_cache_and_names(self):
        self.h.start(["tick"])
        other = Harness(self.directory)
        other.start()
        self.assertTrue(other.session.cache_hit)
        self.assertNotIn(MsgType.MSG_GET_DEFINITIONS, [frame[2] for frame in other.sent])
        self.assertEqual(other.codec.log_mask, b"\x01" + bytes(4))

    def test_device_info_poll_does_not_restart_discovery_or_logging(self):
        self.h.start(["tick"])
        before = len(self.h.sent)
        mask = self.h.codec.log_mask
        self.h.session.send(Packet(MsgType.MSG_GET_DEVICE_INFO, None))
        self.h.drain()
        self.assertEqual(len(self.h.sent), before + 1)
        self.assertEqual(self.h.session.phase, "ready")
        self.assertEqual(self.h.ready, [True])
        self.assertEqual(self.h.codec.log_mask, mask)
        self.assertTrue(self.h.simulator._logging)
        reply = self.h.codec.decode_packet(RawPacket(MsgType.MSG_DEVICE_INFO_REPLY, self.h.simulator.info.encode()))
        self.assertEqual(reply.data.firmware, (1, 0, 0))

    def test_changed_device_info_requires_reconnect(self):
        self.h.start(["tick"])
        self.h.simulator.info = replace(self.h.simulator.info, fingerprint=0)
        self.h.session.send(Packet(MsgType.MSG_GET_DEVICE_INFO, None))
        self.h.drain()
        self.assertFalse(self.h.session.active)
        self.assertIn("reconnect", self.h.errors[-1])

    def test_reordered_ids_resolve_saved_names(self):
        self.h.start(["tick"])
        data = fixture()
        data["signals"][0], data["signals"][8] = data["signals"][8], data["signals"][0]
        data["variables"].reverse()
        data["pids"].reverse()
        other = Harness(self.directory, data)
        other.start()
        self.assertFalse(other.session.cache_hit)
        self.assertEqual(other.codec.log_mask, b"\x00\x01" + bytes(3))
        self.assertEqual(other.codec.variable_list[0]["name"], data["variables"][0]["name"])
        other.session.send(Packet(MsgType.MSG_GET_VAR, VarPayload(0, None)))
        other.session.send(Packet(MsgType.MSG_GET_PID, PIDPayload(0, None, None, None)))
        other.drain()
        self.assertFalse(other.errors)
        other.simulator._emit_log_packet()
        self.assertEqual(set(other.logs[-1].signals), {"tick"})

    def test_missing_saved_name_is_ignored(self):
        self.h.start(["removed_signal", "tick"])
        self.assertEqual([entry["name"] for entry in self.h.codec.get_enabled_signals()], ["tick"])

    def test_corrupt_cache_is_redownloaded(self):
        self.h.start(["tick"])
        cache = DefinitionCache(self.directory)
        cache.path(self.h.session.info).write_text("{broken")
        other = Harness(self.directory)
        other.start(["tick"])
        self.assertFalse(other.session.cache_hit)

    def test_valid_json_with_bad_fingerprint_is_redownloaded(self):
        self.h.start(["tick"])
        cache = DefinitionCache(self.directory)
        path = cache.path(self.h.session.info)
        data = json.loads(path.read_text())
        data["definitions"]["signals"][0]["name"] = "wrong"
        path.write_text(json.dumps(data))
        other = Harness(self.directory)
        other.start(["tick"])
        self.assertFalse(other.session.cache_hit)

    def test_empty_variable_and_pid_lists(self):
        data = fixture()
        data["variables"] = data["pids"] = []
        other = Harness(self.directory, data)
        other.start(["tick"])
        self.assertEqual(other.codec.variable_list, [])
        self.assertEqual(other.codec.pid_list, [])

    def test_full_supported_signal_count_uses_32_byte_mask(self):
        data = fixture()
        data["signals"] = [{"name": f"signal_{index}", "type": "u32"} for index in range(256)]
        other = Harness(self.directory, data)
        other.start(["signal_255"])
        self.assertEqual(other.codec.mask_bytes, 32)
        self.assertEqual(other.codec.log_mask, bytes(31) + b"\x80")
        with self.assertRaises(ValueError):
            other.codec.set_log_mask(b"\xff" * 32)

    def begin_manual(self):
        self.h.session.start(["tick"])
        self.h.session.receive(RawPacket(MsgType.MSG_DEVICE_INFO_REPLY, self.h.simulator.info.encode()))
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))

    def test_unknown_list_and_index_have_explicit_errors(self):
        replies = []
        simulator = self.h.simulator
        simulator.data_callback = lambda buffer: replies.extend(simulator.codec.extract_packets(buffer))
        for payload, code in ((struct.pack("<BH", 3, 0), 1), (struct.pack("<BH", 0, 999), 2)):
            simulator._handle_packet(RawPacket(MsgType.MSG_GET_DEFINITIONS, payload))
            self.assertEqual(replies[-1].msg_type_int, MsgType.MSG_DEFINITIONS_ERROR)
            error = CommandErrorPayload.decode(replies[-1].payload_bytes)
            self.assertEqual(error.command, MsgType.MSG_GET_DEFINITIONS)
            self.assertEqual(error.code, CommandErrorCode.UNKNOWN_DEFINITION_LIST if code == 1
                             else CommandErrorCode.DEFINITION_INDEX)
            self.assertTrue(error.message)

    def test_malformed_or_nonprogressing_page_fails(self):
        self.begin_manual()
        self.h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY, PAGE_HEADER.pack(0, 0, 36, 0)))
        self.assertTrue(self.h.errors)
        self.assertFalse(self.h.ready)

    def test_wrong_page_index_fails(self):
        self.begin_manual()
        self.h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY,
            PAGE_HEADER.pack(0, 1, 36, 1) + b"1\tf\twrong\n"))
        self.assertTrue(self.h.errors)

    def test_duplicate_rows_rejected_before_cache_write(self):
        self.begin_manual()
        rows = b"".join(f"{index}\tu32\tduplicate\n".encode() for index in range(36))
        self.h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY, PAGE_HEADER.pack(0, 0, 36, 36) + rows))
        for kind in (1, 2):
            text = self.h.simulator.definitions.text(kind).encode()
            count = self.h.simulator.info.counts[kind]
            self.h.session.receive(RawPacket(MsgType.MSG_DEFINITIONS_REPLY, PAGE_HEADER.pack(kind, 0, count, count) + text))
        self.assertTrue(self.h.errors)
        self.assertEqual(list(Path(self.directory).glob("definitions-v*.json")), [])

    def test_bad_info_or_invalid_driver_definitions_stop_initialization(self):
        for info in (replace(self.h.simulator.info, protocol=255), replace(self.h.simulator.info, status=1),
                     replace(self.h.simulator.info, mask_bytes=8)):
            self.h.session.start()
            self.h.session.receive(RawPacket(MsgType.MSG_DEVICE_INFO_REPLY, info.encode()))
            self.assertFalse(self.h.session.active)
        self.assertFalse(self.h.ready)

    def test_timeout_discards_connection_ids(self):
        self.h.session.clock = lambda: 0
        self.h.session.start()
        self.h.session.clock = lambda: 3
        self.h.session.tick()
        self.assertTrue(self.h.errors)
        self.assertFalse(self.h.session.active)
        self.assertEqual(self.h.codec.variable_list, [])

    def test_rejected_mask_cancels_start(self):
        self.h.start(["tick"])
        states = []
        self.h.session.logging_changed = states.append
        self.h.session.send(Packet(MsgType.MSG_STOP_LOG, None))
        self.h.session.send(Packet(MsgType.MSG_SET_MASK, bytes(5)))
        self.h.session.send(Packet(MsgType.MSG_START_LOG, None))
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        self.h.session.receive(error_reply(MsgType.MSG_SET_MASK, CommandErrorCode.LOGGING_ACTIVE))
        self.assertFalse(self.h.errors)
        self.assertTrue(self.h.rejections)
        self.assertTrue(self.h.session.active)
        self.assertIsNone(self.h.session.pending)
        self.assertFalse(self.h.session.commands)
        self.assertIsNone(self.h.session.requested_mask)
        self.assertEqual(states, [False])

    def test_rejected_run_keeps_connection_and_processes_next_command(self):
        self.h.start(["tick"])
        definitions, info = self.h.session.definitions, self.h.session.info
        mask = self.h.codec.log_mask
        self.h.session.send(Packet(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN)))
        self.assertEqual(self.h.sent[-1], b"\xaa\x55\x15\x01\x00\x0c")
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.h.session.receive(error_reply(MsgType.MSG_SET_STATE, CommandErrorCode.ENCODER_NOT_ALIGNED,
                                          "Encoder is not aligned."))
        self.assertEqual(len(self.h.rejections), 1)
        self.assertIn("ENCODER_NOT_ALIGNED", self.h.rejections[0])
        self.assertIn("Encoder is not aligned.", self.h.rejections[0])
        self.assertFalse(self.h.errors)
        self.assertTrue(self.h.session.active)
        self.assertEqual(self.h.session.phase, "ready")
        self.assertIs(self.h.session.definitions, definitions)
        self.assertIs(self.h.session.info, info)
        self.assertEqual(self.h.codec.log_mask, mask)
        self.assertEqual(self.h.sent[-1][2], MsgType.MSG_GET_STATE)
        self.h.session.receive(RawPacket(MsgType.MSG_STATE_REPLY, bytes((FOCState.FOC_STATE_IDLE,))))
        self.assertIsNone(self.h.session.pending)
        self.h.session.clock = lambda: self.h.session.deadline + 1
        self.h.session.tick()
        self.assertTrue(self.h.session.active)
        self.h.simulator._emit_log_packet()
        self.assertEqual(set(self.h.logs[-1].signals), {"tick"})

    def test_runtime_rejection_clears_pending_and_accepts_new_commands(self):
        self.h.start(["tick"])
        for reply in (MsgType.MSG_ERROR, MsgType.MSG_INVALID_PAYLOAD,
                      MsgType.MSG_UNKNOWN_ID, MsgType.MSG_UNKNOWN_TYPE):
            with self.subTest(reply=reply):
                self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
                self.h.session.receive(error_reply(MsgType.MSG_GET_STATE, response=reply))
                self.assertTrue(self.h.session.active)
                self.assertIsNone(self.h.session.pending)
                self.assertFalse(self.h.errors)
                self.assertIn("MSG_GET_STATE rejected", self.h.rejections[-1])
                self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
                self.h.session.receive(RawPacket(MsgType.MSG_STATE_REPLY, b"\x0e"))
                self.assertIsNone(self.h.session.pending)

    def test_rejected_stop_cancels_mask_and_restart(self):
        self.h.start(["tick"])
        self.h.session.send(Packet(MsgType.MSG_STOP_LOG, None))
        self.h.session.send(Packet(MsgType.MSG_SET_MASK, bytes(5)))
        self.h.session.send(Packet(MsgType.MSG_START_LOG, None))
        sent = len(self.h.sent)
        self.h.session.receive(error_reply(MsgType.MSG_STOP_LOG))
        self.assertTrue(self.h.session.active)
        self.assertFalse(self.h.session.commands)
        self.assertIsNone(self.h.session.pending)
        self.assertEqual(len(self.h.sent), sent)

    def test_initialization_rejection_still_disconnects(self):
        self.h.session.start(["tick"])
        self.h.session.receive(error_reply(MsgType.MSG_GET_DEVICE_INFO))
        self.assertFalse(self.h.session.active)
        self.assertTrue(self.h.errors)
        self.assertFalse(self.h.rejections)

    def test_runtime_timeout_still_disconnects(self):
        self.h.start(["tick"])
        self.h.session.clock = lambda: 0
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.h.session.clock = lambda: 3
        self.h.session.tick()
        self.assertFalse(self.h.session.active)
        self.assertTrue(self.h.errors)
        self.assertFalse(self.h.rejections)

    def test_malformed_error_and_buffer_overflow_still_disconnect(self):
        for reply, payload in ((MsgType.MSG_ERROR, b"unexpected"),
                               (MsgType.MSG_BUFFER_OVERFLOW,
                                error_reply(0xFF, CommandErrorCode.RECEIVE_OVERFLOW).payload_bytes)):
            with self.subTest(reply=reply):
                self.h.start(["tick"])
                self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
                self.h.session.receive(RawPacket(reply, payload))
                self.assertFalse(self.h.session.active)
                self.assertTrue(self.h.errors)
                self.h.errors.clear()

    def test_mask_mismatch_and_malformed_reply_fail(self):
        self.h.start(["tick"])
        self.h.session.send(Packet(MsgType.MSG_SET_MASK, bytes(5)))
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        self.h.session.receive(RawPacket(MsgType.MSG_MASK_REPLY, b"\x01" + bytes(4)))
        self.assertTrue(self.h.errors)

    def test_logging_confirmation_and_single_command(self):
        self.h.start(["tick"])
        states = []
        self.h.session.logging_changed = states.append
        self.h.session.send(Packet(MsgType.MSG_STOP_LOG, None))
        self.h.session.send(Packet(MsgType.MSG_GET_STATE, None))
        self.assertFalse(states)
        self.assertEqual(self.h.sent[-1][2], MsgType.MSG_STOP_LOG)
        self.h.session.receive(RawPacket(MsgType.MSG_ACK, b""))
        self.assertEqual(states, [False])
        self.assertEqual(self.h.sent[-1][2], MsgType.MSG_GET_STATE)


class DefinitionValidationTests(unittest.TestCase):
    def test_duplicate_names_and_ids(self):
        for key in ("name", "bit"):
            data = definitions_from(fixture())
            data.signals[1][key] = data.signals[0][key]
            with self.assertRaises(ValueError):
                data.validate()

    def test_fingerprint_tracks_order_names_and_types(self):
        original = definitions_from(fixture())
        fingerprint = original.fingerprint()
        for mutate in (lambda data: data["signals"].reverse(),
                       lambda data: data["variables"][0].update(type="i32"),
                       lambda data: data["pids"][0].update(name="renamed")):
            data = fixture()
            mutate(data)
            self.assertNotEqual(fingerprint, definitions_from(data).fingerprint())

    def test_same_name_across_lists_is_allowed(self):
        data = fixture()
        data["variables"][0]["name"] = data["signals"][0]["name"]
        definitions_from(data).validate()

    def test_malformed_text(self):
        for text in ("0\tf\tmissing_newline", "0\tunknown\tvalue\n", "0\tf\tbad name\n"):
            with self.assertRaises(ValueError):
                rows = parse_rows(0, text)
                Definitions(rows, [], []).validate()


class FirmwareContractTests(unittest.TestCase):
    def test_c_lists_match_simulator_fixture_and_protocol(self):
        firmware = Path(os.environ.get("RAPID_DRIVE_FIRMWARE", ROOT.parent / "rapid-drive-foc"))
        source = firmware / "Foc/FOC_Core/FOC_USB_Debug.c"
        if not source.exists():
            self.skipTest("Set RAPID_DRIVE_FIRMWARE to check the firmware contract")
        text = source.read_text()
        actual = []
        for kind, name in enumerate(("FOC_USB_DEBUG_SIGNAL_LIST", "VAR_ID_LIST", "FOC_PID_CONTROLLERS_LIST")):
            block = re.search(r"#define " + name + r"\(X\)(.*?)(?:\n\s*\n)", text, re.S).group(1)
            pairs = re.findall(r"X\((?:(f|u32|i32),\s*)?([\w.]+)\)", block)
            entries = []
            for index, (value_type, field) in enumerate(pairs):
                entry = {"bit" if kind == 0 else "id": index, "name": field}
                if kind != 2:
                    entry["type"] = value_type
                entries.append(entry)
            actual.append(entries)
        expected = definitions_from(fixture())
        self.assertEqual(Definitions(*actual).fingerprint(), expected.fingerprint())
        header = (firmware / "Foc/FOC_Core/FOC_USB_Debug.h").read_text()
        self.assertIn(f"#define DEBUG_PROTOCOL_VERSION {PROTOCOL_VERSION}", header)
        for name, value in re.findall(r"\b(MSG_\w+)\s*=\s*(0x[0-9A-Fa-f]+)", header):
            self.assertEqual(int(getattr(MsgType, name)), int(value, 16), name)
        codes = re.findall(r"\bDEBUG_ERR_(\w+)\s*=\s*(\d+)", header)
        self.assertEqual(len(codes), len(CommandErrorCode))
        for name, value in codes:
            self.assertEqual(int(getattr(CommandErrorCode, name)), int(value), name)


if __name__ == "__main__":
    unittest.main()
