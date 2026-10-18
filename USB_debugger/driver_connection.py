"""One transport generation with private parser, requests, and notifications."""
import queue
import threading

from protocol_codec import LogPayload, ProtocolCodec
from protocol_definitions import MsgType
from protocol_session import ProtocolSession


class DriverConnection:
    """Retain transport ownership until its thread has actually terminated.

    GUI notifications live in this connection's queue. A replacement never
    reads that queue or reuses the old codec/session. Worker callbacks only
    touch their own generation, including callbacks delivered after closing.
    """

    def __init__(self, worker_factory, selected_names=None, *, on_log=None,
                 on_ready=None, sample_rate=8000, cache=None, selections=None):
        self.codec = ProtocolCodec()
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self._closing = threading.Event()
        self._disconnected = threading.Event()
        self._selected_names = selected_names
        self._on_log = on_log or (lambda connection, payload: None)
        self._on_ready = on_ready or (lambda connection: None)
        self.session = ProtocolSession(
            self.codec, self.commands.put, self._ready,
            lambda message: self.events.put(("failed", message)), sample_rate,
            logging_changed=lambda active: self.events.put(("logging", active)),
            definitions_ready=lambda definitions, info, mask:
                self.events.put(("definitions", (definitions, info, mask))),
            rejected=lambda message: self.events.put(("rejected", message)),
            cache=cache, selections=selections,
        )
        self.worker = worker_factory(
            command_queue=self.commands,
            data_callback=self._receive,
            error_callback=self._transport_error,
            disconnect_callback=self._disconnect,
            ready_callback=self._transport_ready,
        )

    @property
    def closed(self):
        return self._closing.is_set()

    def start(self):
        self.worker.start()

    def send(self, packet, completed=None):
        if self.closed or self._disconnected.is_set():
            if completed is not None:
                completed(False)
            return
        self.session.send(packet, completed=completed)

    def tick(self):
        if not self.closed and not self._disconnected.is_set():
            self.session.tick()

    def stop(self, timeout=2.0):
        """Close and join before allowing reuse of the serial endpoint.

        A caller must retain this object and refuse to open a replacement if
        the bounded join fails; a later attempt can finish the same cleanup.
        """
        self._closing.set()
        self.session.stop()
        self.worker.stop()
        thread = self.worker.thread
        if thread.ident is not None and thread is not threading.current_thread():
            self.worker.join(timeout=timeout)
            if thread.is_alive():
                raise RuntimeError("Previous serial worker is still closing; try reconnecting again shortly.")

    def _transport_ready(self):
        with self.session.lock:
            if self.closed or self._disconnected.is_set():
                return
            self.session.start(self._selected_names)

    def _ready(self):
        if self.closed or self._disconnected.is_set():
            return
        # Recording metadata is updated before this worker emits telemetry.
        self._on_ready(self)
        self.events.put(("ready", self.session.sample_rate))

    def _transport_error(self, error):
        if self.closed or self._disconnected.is_set():
            return
        with self.session.lock:
            phase = self.session.phase
            self.session.stop()
        self.events.put(("failed", f"Serial connection failed during {phase}: {error}"))

    def _disconnect(self):
        self._disconnected.set()
        self.session.stop()
        if not self.closed:
            self.events.put(("disconnected", None))

    def _receive(self, rx_buffer):
        with self.session.lock:
            if self.closed or self._disconnected.is_set() or not self.session.active:
                rx_buffer.clear()
                return
            for raw in self.codec.extract_packets(rx_buffer):
                if self.closed or self._disconnected.is_set():
                    rx_buffer.clear()
                    return
                self.session.receive(raw)
                if not self.session.active:
                    rx_buffer.clear()
                    return
                if raw.msg_type_int == MsgType.MSG_DEFINITIONS_REPLY:
                    continue
                if raw.msg_type_int == MsgType.MSG_LOG_DATA and self.session.initializing:
                    continue
                decoded = self.codec.decode_packet(raw)
                if decoded is not None and isinstance(decoded.data, LogPayload):
                    self._on_log(self, decoded.data)
                elif decoded is not None:
                    self.events.put(("reply", decoded))
