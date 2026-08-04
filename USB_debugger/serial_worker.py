import time
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import serial
from serial import SerialException
from serial.tools import list_ports


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 115200
    timeout: float = 0.1


class SerialWorker:
    def __init__(
        self,
        config: SerialConfig,
        command_queue: queue.Queue[bytes],
        data_callback: Optional[Callable[[bytearray], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
    ):
        self.config = config
        self.command_queue = command_queue
        self.data_callback = data_callback
        self.error_callback = error_callback

        self.ser: Optional[serial.Serial] = None
        self.rx_buffer = bytearray()
        self.stop_flag = False
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

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
            finally:
                self.ser = None

    def stop(self) -> None:
        self.stop_flag = True

    def send_bytes(self, data: bytes) -> None:
        if self.ser is None or not self.ser.is_open:
            raise SerialException("Serial port is not open")
        self.ser.write(data)

    def _process_command_queue(self) -> None:
        while True:
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                break

            try:
                self.send_bytes(cmd)
            except Exception as e:
                if self.error_callback:
                    self.error_callback(e)

    def _handle_buffer(self) -> None:
        if self.data_callback:
            self.data_callback(self.rx_buffer)

    def _run(self) -> None:
        try:
            self.open()

            while not self.stop_flag:
                self._process_command_queue()

                try:
                    data = self.ser.read(256) if self.ser is not None else b""
                except SerialException as e:
                    if self.error_callback:
                        self.error_callback(e)
                    break

                if data:
                    self.rx_buffer.extend(data)
                    self._handle_buffer()
                else:
                    time.sleep(0.001)

        except Exception as e:
            if self.error_callback:
                self.error_callback(e)
        finally:
            self.close()