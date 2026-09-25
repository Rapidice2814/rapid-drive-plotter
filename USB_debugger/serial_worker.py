import queue
import time
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import serial
from serial import SerialException
from serial.tools import list_ports

from config import COMMAND_RATE_LIMIT_SECONDS


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 115200
    timeout: float = 0.1
    command_interval: float = 0.1
    reply_timeout: float = 1.0


@dataclass
class CommandRequest:
    """One wire command plus the reply types that complete it."""

    packet: Packet
    wire_bytes: bytes
    expected_replies: frozenset[MsgType]
    description: str


class SerialWorker:
    def __init__(
        self,
        config: SerialConfig,
        command_queue: queue.Queue[CommandRequest],
        data_callback: Optional[Callable[[bytearray], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
        disconnect_callback: Optional[Callable[[], None]] = None,
        ready_callback: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.command_queue = command_queue
        self.data_callback = data_callback
        self.error_callback = error_callback
        self.disconnect_callback = disconnect_callback
        self.ready_callback = ready_callback

        self.ser: Optional[serial.Serial] = None
        self.rx_buffer = bytearray()
        self.stop_flag = False
        self._command_rate_limit_seconds = max(
            0.0, float(COMMAND_RATE_LIMIT_SECONDS)
        )
        self._next_command_at = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def list_available_ports() -> list[str]:
        return [port.device for port in list_ports.comports()]

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout=timeout)

    def open(self) -> None:
        self.ser = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baudrate,
            timeout=self.config.timeout,
        )
        self._clear_command_queue()
        self.pending_command = None
        self._queued_command = None
        self._pending_sent_at = None
        self._last_command_sent_at = None

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
            finally:
                self.ser = None

    def stop(self) -> None:
        self.stop_flag = True
        self.close()

    def send_bytes(self, data: bytes) -> None:
        if self.ser is None or not self.ser.is_open:
            raise SerialException("Serial port is not open")
        self.ser.write(data)

    def _process_command_queue(self) -> None:
        if self._command_rate_limit_seconds > 0:
            # Pace queued requests instead of flooding the firmware's small
            # receive buffer. The read loop continues between sends.
            if time.monotonic() < self._next_command_at:
                return
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                return

            try:
                self.send_bytes(cmd)
            except Exception as e:
                if self.error_callback:
                    self.error_callback(e)
            finally:
                self._next_command_at = (
                    time.monotonic() + self._command_rate_limit_seconds
                )
            return

        # A zero interval deliberately preserves the original behavior.
        while True:
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                break

    def _complete_pending(
        self,
        reply: Optional[Packet],
        ok: bool,
        reason: str = "",
    ) -> None:
        request = self.pending_command
        if request is None:
            return

        self.pending_command = None
        self._pending_sent_at = None
        self.command_queue.task_done()
        self._notify_command_result(request, reply, ok, reason)

    def _try_send_next_command(self) -> None:
        if self.pending_command is not None:
            return

        if self._queued_command is None:
            try:
                self._queued_command = self.command_queue.get_nowait()
            except queue.Empty:
                return

        now = time.monotonic()
        if self._last_command_sent_at is not None:
            elapsed = now - self._last_command_sent_at
            if elapsed < self.config.command_interval:
                return

        request = self._queued_command
        self._queued_command = None
        assert request is not None

        try:
            self.send_bytes(request.wire_bytes)
        except Exception as exc:
            self.command_queue.task_done()
            self._last_command_sent_at = now
            self._notify_command_result(request, None, False, str(exc))
            if self.error_callback:
                self.error_callback(exc)
            return

        self.pending_command = request
        self._pending_sent_at = now
        self._last_command_sent_at = now

    def _check_reply_timeout(self) -> None:
        if self.pending_command is None or self._pending_sent_at is None:
            return

        if time.monotonic() - self._pending_sent_at >= self.config.reply_timeout:
            self._complete_pending(
                None,
                False,
                f"Timed out after {self.config.reply_timeout:.3g} s waiting for a reply",
            )

    def handle_reply(self, packet: Packet) -> None:
        """Match the next non-telemetry packet to the command in flight."""
        if packet.msg_type == MsgType.MSG_LOG_DATA:
            return

        request = self.pending_command
        if request is None:
            # There is no command to correlate this packet with.  It is left
            # for the normal decoder/UI path, but must not unblock a command.
            return

        if packet.msg_type in ERROR_REPLY_TYPES:
            self._complete_pending(
                packet,
                False,
                f"Device returned {getattr(packet.msg_type, 'name', packet.msg_type)}",
            )
            return

        if not packet.valid:
            self._complete_pending(
                packet,
                False,
                f"Invalid payload in {getattr(packet.msg_type, 'name', packet.msg_type)}",
            )
            return

        if packet.msg_type not in request.expected_replies:
            expected = ", ".join(msg.name for msg in request.expected_replies)
            self._complete_pending(
                packet,
                False,
                f"Unexpected reply {getattr(packet.msg_type, 'name', packet.msg_type)}; expected {expected}",
            )
            return

        self._complete_pending(packet, True)

    def _handle_buffer(self) -> None:
        if self.data_callback:
            self.data_callback(self.rx_buffer)

    def _notify_disconnect(self) -> None:
        if self.disconnect_callback:
            self.disconnect_callback()

    def _clear_command_queue(self) -> None:
        if self._queued_command is not None:
            request = self._queued_command
            self._queued_command = None
            self.command_queue.task_done()
            self._notify_command_result(
                request,
                None,
                False,
                "Command discarded while opening the serial port",
            )

        while True:
            try:
                request = self.command_queue.get_nowait()
            except queue.Empty:
                break
            self.command_queue.task_done()
            self._notify_command_result(
                request,
                None,
                False,
                "Command discarded while opening the serial port",
            )

    def _run(self) -> None:
        try:
            self.open()
            # The port is open and stale queued commands are gone. Notify the
            # GUI-prepared initialization commands before processing traffic.
            if self.ready_callback is not None:
                self.ready_callback()

            while not self.stop_flag:
                self._try_send_next_command()
                self._check_reply_timeout()

                try:
                    data = self.ser.read(256) if self.ser is not None else b""
                except (SerialException, OSError) as e:
                    if self.error_callback:
                        self.error_callback(e)
                    self._notify_disconnect()
                    break

                if data:
                    self.rx_buffer.extend(data)
                    self._handle_buffer()
                else:
                    if self.ser is not None and not self.ser.is_open:
                        self._notify_disconnect()
                        break
                    time.sleep(0.001)

        except Exception as e:
            if self.error_callback:
                self.error_callback(e)
            self._notify_disconnect()
        finally:
            if self.pending_command is not None:
                self._complete_pending(
                    None,
                    False,
                    "Serial worker stopped before the command received a reply",
                )
            self.close()
