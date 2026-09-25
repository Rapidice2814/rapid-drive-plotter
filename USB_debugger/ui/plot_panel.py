"""Live plot widget and bounded hand-off from the serial worker."""

from __future__ import annotations

import logging
import queue
from collections import deque
from typing import TypeAlias

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget

from config import TIMESTAMP_HZ
from protocol_codec import LogPayload

_LOG = logging.getLogger(__name__)
_MAX_BUFFER_SAMPLES = 100_000
_MAX_QUEUED_PAYLOADS = 512
_MAX_PAYLOADS_PER_TICK = 500
_UPDATE_INTERVAL_MS = 50

NumericBuffer: TypeAlias = deque[int | float]


class PlotPanel(QWidget):
    """Plot selected log signals with bounded memory and GUI-thread updates.

    ``enqueue_log`` is safe to call from the serial worker thread. All plot
    objects are only touched by the Qt timer on the GUI thread.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        max_samples: int = _MAX_BUFFER_SAMPLES,
        max_queued_payloads: int = _MAX_QUEUED_PAYLOADS,
        update_interval_ms: int = _UPDATE_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        if max_samples <= 0 or max_queued_payloads <= 0 or update_interval_ms <= 0:
            raise ValueError("buffer sizes and update interval must be positive")
        if TIMESTAMP_HZ <= 0:
            raise ValueError("TIMESTAMP_HZ must be positive")

        self._max_samples = max_samples
        self._payload_queue: queue.Queue[LogPayload] = queue.Queue(
            maxsize=max_queued_payloads
        )
        self.dropped_payloads = 0
        self._signal_names: tuple[str, ...] = ()
        self._last_timestamp_raw: int | None = None
        self._timestamp_wrap_offset = 0
        self.time_buffer: deque[float] = deque(maxlen=max_samples)
        self.data_buffers: dict[str, NumericBuffer] = {}
        self.curves: dict[str, pg.PlotDataItem] = {}

        layout = QVBoxLayout(self)
        self.graph = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graph)

        self.plot = self.graph.addPlot(title="Live Data")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.addLegend()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue_and_update)
        self.timer.start(update_interval_ms)

    @property
    def active_signals(self) -> tuple[str, ...]:
        """Signal names currently represented by the incoming payloads."""
        return self._signal_names

    def enqueue_log(self, payload: LogPayload) -> None:
        """Queue a decoded payload; discard the oldest plot-only data on overload."""
        try:
            self._payload_queue.put_nowait(payload)
        except queue.Full:
            try:
                self._payload_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._payload_queue.put_nowait(payload)
            except queue.Full:
                self.dropped_payloads += 1
                return
            self.dropped_payloads += 1

    def _drain_queue_and_update(self) -> None:
        updated = False
        for _ in range(_MAX_PAYLOADS_PER_TICK):
            try:
                payload = self._payload_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_log_payload(payload)
            updated = True

        if updated:
            self._update_plot()

    def _handle_log_payload(self, payload: LogPayload) -> None:
        if payload.sample_count < 0:
            _LOG.warning("Ignoring log payload with negative sample count")
            return

        names = tuple(payload.signals)
        if payload.signal_count != len(names):
            _LOG.warning(
                "Ignoring log payload: signal_count=%d but %d signal arrays supplied",
                payload.signal_count,
                len(names),
            )
            return
        if any(len(values) != payload.sample_count for values in payload.signals.values()):
            _LOG.warning("Ignoring log payload with signal arrays of inconsistent length")
            return
        if payload.sample_count == 0:
            return

        if names != self._signal_names:
            self._reset_signal_history(names)

        # Timestamp is an unsigned 32-bit device tick count. Unwrap rollover so
        # the plotted time remains monotonic during long-running captures.
        timestamp = payload.timestamp
        if not 0 <= timestamp <= 0xFFFFFFFF:
            _LOG.warning("Ignoring log payload with invalid timestamp: %s", timestamp)
            return
        if (
            self._last_timestamp_raw is not None
            and timestamp < self._last_timestamp_raw
        ):
            if self._last_timestamp_raw - timestamp > 0x80000000:
                self._timestamp_wrap_offset += 0x1_0000_0000
            else:
                # The device timer restarted without a new signal mask.
                self.time_buffer.clear()
                for buffer in self.data_buffers.values():
                    buffer.clear()
                for curve in self.curves.values():
                    curve.setData([], [])
                self._timestamp_wrap_offset = 0
        self._last_timestamp_raw = timestamp
        unwrapped_timestamp = self._timestamp_wrap_offset + timestamp

        # Use tick offsets before converting to seconds to avoid cumulative
        # floating-point drift within each packet.
        first_time = unwrapped_timestamp / TIMESTAMP_HZ
        self.time_buffer.extend(
            first_time + sample_index / TIMESTAMP_HZ
            for sample_index in range(payload.sample_count)
        )
        for name in self._signal_names:
            self.data_buffers[name].extend(payload.signals[name])

    def _reset_signal_history(self, names: tuple[str, ...]) -> None:
        """Start a fresh aligned history when the device changes its log mask."""
        self._signal_names = names
        self.time_buffer.clear()
        self.data_buffers = {
            name: deque(maxlen=self._max_samples) for name in names
        }

        for name in tuple(self.curves):
            if name not in names:
                self.plot.removeItem(self.curves.pop(name))
            else:
                self.curves[name].setData([], [])

        for name in names:
            if name not in self.curves:
                self.curves[name] = self.plot.plot(
                    pen=pg.intColor(len(self.curves)),
                    name=name,
                )

    def clear_data(self) -> None:
        """Clear plot history and pending payloads, e.g. before reconnecting."""
        while True:
            try:
                self._payload_queue.get_nowait()
            except queue.Empty:
                break
        self.time_buffer.clear()
        for buffer in self.data_buffers.values():
            buffer.clear()
        for curve in self.curves.values():
            curve.setData([], [])
        self._last_timestamp_raw = None
        self._timestamp_wrap_offset = 0

    def _update_plot(self) -> None:
        if not self._signal_names or not self.time_buffer:
            return

        x = np.fromiter(self.time_buffer, dtype=np.float64)
        for name in self._signal_names:
            y = np.fromiter(self.data_buffers[name], dtype=np.float64)
            # These buffers are reset together whenever the selected signal set
            # changes, so unequal sizes indicate malformed input/state.
            if x.size != y.size:
                _LOG.error("Plot buffers lost alignment for signal %s", name)
                continue
            self.curves[name].setData(x, y)

    def reset_zoom(self) -> None:
        self.plot.autoRange()
