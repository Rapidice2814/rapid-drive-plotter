import time
import queue
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
    write_timeout: float = 1.0


class SerialWorker:
    def __init__(
        self,
        config: SerialConfig,
        command_queue: queue.Queue[bytes],
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
        self._disconnect_notified = False
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
            write_timeout=self.config.write_timeout,
        )
        self.ser.reset_input_buffer()
        self.rx_buffer.clear()
        self._clear_command_queue()

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
            finally:
                self.ser = None

    def stop(self) -> None:
        self.stop_flag = True
        # The worker owns closing the handle. Cancel outstanding I/O so its
        # finally block can close before a replacement opens the same port.
        port = self.ser
        if port is not None:
            for name in ("cancel_read", "cancel_write"):
                cancel = getattr(port, name, None)
                if cancel is not None:
                    try:
                        cancel()
                    except (SerialException, OSError):
                        pass

    def send_bytes(self, data: bytes) -> None:
        if self.ser is None or not self.ser.is_open:
            raise SerialException("Serial port is not open")
        written = self.ser.write(data)
        if written != len(data):
            raise SerialException(f"Incomplete serial write: {written} of {len(data)} bytes")

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

            self.send_bytes(cmd)
            self._next_command_at = time.monotonic() + self._command_rate_limit_seconds
            return

        # A zero interval deliberately preserves the original behavior.
        while not self.stop_flag:
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                break

            self.send_bytes(cmd)

    def _handle_buffer(self) -> None:
        if self.data_callback:
            self.data_callback(self.rx_buffer)

    def _notify_disconnect(self) -> None:
        if not self._disconnect_notified and self.disconnect_callback:
            self._disconnect_notified = True
            self.disconnect_callback()

    def _clear_command_queue(self) -> None:
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

    def _run(self) -> None:
        try:
            self.open()
            if self.stop_flag:
                return
            # The port is open and stale queued commands are gone. Notify the
            # GUI-prepared initialization commands before processing traffic.
            if self.ready_callback is not None:
                self.ready_callback()

            while not self.stop_flag:
                self._process_command_queue()
                if self.stop_flag:
                    break
                data = self.ser.read(256) if self.ser is not None else b""
                if self.stop_flag:
                    break

                if data:
                    self.rx_buffer.extend(data)
                    self._handle_buffer()
                else:
                    if self.ser is not None and not self.ser.is_open:
                        raise SerialException("Serial port closed unexpectedly")
                    time.sleep(0.001)

        except Exception as e:
            if not self.stop_flag and self.error_callback:
                self.error_callback(e)
        finally:
            try:
                self.close()
            finally:
                self._notify_disconnect()
