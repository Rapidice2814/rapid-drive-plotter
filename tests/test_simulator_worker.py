import queue
import threading
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "USB_debugger"))
sys.path.insert(0, str(PROJECT_ROOT))

from Serial_Simulator.simulator_worker import SimulatorConfig, SimulatorWorker
from protocol_codec import Packet, PIDPayload, ProtocolCodec, TextPayload, VarPayload
from protocol_definitions import MsgType


class SimulatorWorkerTests(unittest.TestCase):
    def setUp(self):
        self.client_codec = ProtocolCodec()
        self.packets = []
        self.worker = SimulatorWorker(
            command_queue=queue.Queue(),
            data_callback=self._capture_packets,
            config=SimulatorConfig(sample_interval=0.05, samples_per_packet=4),
        )

    def _capture_packets(self, rx_buffer):
        self.packets.extend(self.client_codec.extract_packets(rx_buffer))

    def _send(self, packet):
        raw = self.client_codec.encode_packet(packet)
        start = len(self.packets)
        self.worker.send_bytes(self.client_codec.build_packet(raw))
        return self.packets[start:]

    def test_get_pid_returns_protocol_reply(self):
        packets = self._send(Packet(
            msg_type=MsgType.MSG_GET_PID,
            data=PIDPayload(controller_id=0, kp=None, ki=None, kd=None),
        ))
        self.assertEqual(len(packets), 1)
        reply = self.client_codec.decode_packet(packets[0])
        self.assertEqual(reply.msg_type, MsgType.MSG_PID_REPLY)
        self.assertAlmostEqual(reply.data.kp, 0.5)

    def test_set_then_get_variable(self):
        self._send(Packet(
            msg_type=MsgType.MSG_SET_VAR,
            data=VarPayload(var_id=3, value=12.5),
        ))
        packets = self._send(Packet(
            msg_type=MsgType.MSG_GET_VAR,
            data=VarPayload(var_id=3, value=None),
        ))
        reply = self.client_codec.decode_packet(packets[0])
        self.assertEqual(reply.msg_type, MsgType.MSG_VAR_REPLY)
        self.assertAlmostEqual(reply.data.value, 12.5)

    def test_text_controls_update_simulated_motor_state(self):
        packets = self._send(Packet(
            msg_type=MsgType.MSG_TEXT_COMMAND,
            data=TextPayload(text="Ms"),
        ))
        mode_reply = self.client_codec.decode_packet(packets[0])
        self.assertEqual(mode_reply.data.text, "Simulator control mode: speed")
        packets = self._send(Packet(
            msg_type=MsgType.MSG_TEXT_COMMAND,
            data=TextPayload(text="Ss1200"),
        ))
        setpoint_reply = self.client_codec.decode_packet(packets[0])
        self.assertEqual(setpoint_reply.data.text, "Simulator Ss setpoint: 1200")
        self.assertEqual(self.worker._variables[3], 1200.0)

    def test_log_packet_obeys_signal_mask(self):
        mask = bytes((0b00000011,)) + bytes(7)
        self._send(Packet(msg_type=MsgType.MSG_SET_MASK, data=mask))
        self.client_codec.set_log_mask(mask)
        start = len(self.packets)
        self.worker._emit_log_packet()
        packets = self.packets[start:]
        self.assertEqual(len(packets), 1)
        reply = self.client_codec.decode_packet(packets[0])
        self.assertEqual(reply.msg_type, MsgType.MSG_LOG_DATA)
        self.assertEqual(reply.data.sample_count, 4)
        self.assertEqual(reply.data.signal_count, 2)
        self.assertEqual(len(reply.data.signals["timestamp"]), 4)
        self.assertGreater(reply.data.signals["adc_values.motor_temp"][0], 0)

    def test_worker_thread_processes_queue_and_stops(self):
        replied = threading.Event()
        packets = []

        def on_data(rx_buffer):
            for raw in self.client_codec.extract_packets(rx_buffer):
                decoded = self.client_codec.decode_packet(raw)
                if decoded is not None:
                    packets.append(decoded)
                    if decoded.msg_type == MsgType.MSG_PID_REPLY:
                        replied.set()

        worker = SimulatorWorker(
            command_queue=queue.Queue(),
            data_callback=on_data,
            config=SimulatorConfig(sample_interval=0.05, samples_per_packet=2),
        )
        worker.start()
        command = Packet(
            msg_type=MsgType.MSG_GET_PID,
            data=PIDPayload(controller_id=1, kp=None, ki=None, kd=None),
        )
        worker.command_queue.put(self.client_codec.build_packet(self.client_codec.encode_packet(command)))
        self.assertTrue(replied.wait(1.0), "simulator did not answer queued command")
        worker.stop()
        worker.join(1.0)
        self.assertFalse(worker.thread.is_alive())
        self.assertAlmostEqual(packets[0].data.kp, 0.45)


if __name__ == "__main__":
    unittest.main()
