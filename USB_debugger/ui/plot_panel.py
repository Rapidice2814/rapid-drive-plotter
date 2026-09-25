"""Live plot widget and bounded hand-off from the serial worker."""

from __future__ import annotations

import logging
import queue
from collections import deque
from typing import TypeAlias

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import SAMPLE_RATE
from protocol_codec import LogPayload

_LOG = logging.getLogger(__name__)
_MAX_BUFFER_SAMPLES = 100_000
_MAX_QUEUED_PAYLOADS = 512
_MAX_PAYLOADS_PER_TICK = 500
_UPDATE_INTERVAL_MS = 50
_DEFAULT_WINDOW_SECONDS = 5.0

NumericBuffer: TypeAlias = deque[int | float]


class _InteractiveViewBox(pg.ViewBox):
    """ViewBox that reports manual mouse navigation to the plot widget."""

    userInteracted = Signal()

    def wheelEvent(self, event, axis=None) -> None:
        self.userInteracted.emit()
        super().wheelEvent(event, axis)

    def mouseDragEvent(self, event, axis=None) -> None:
        if event.isStart():
            self.userInteracted.emit()
        super().mouseDragEvent(event, axis)


class PlotPanel(QWidget):
    """Live chart with optional auto-follow and a sample-based time axis.

    ``enqueue_log`` is safe to call from the serial worker thread. Plot objects
    are only touched on the GUI thread by the timer. X values are generated from
    received sample counts; the device-provided timestamp is intentionally not
    used, so a device restart cannot move the plotted time backwards.
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
        if SAMPLE_RATE <= 0:
            raise ValueError("SAMPLE_RATE must be positive")

        self._max_samples = max_samples
        self._payload_queue: queue.Queue[LogPayload] = queue.Queue(
            maxsize=max_queued_payloads
        )
        self.dropped_payloads = 0
        self._signal_names: tuple[str, ...] = ()
        self._samples_received = 0
        self._auto_follow = True
        self.logging_active = False
        self.time_buffer: deque[float] = deque(maxlen=max_samples)
        self.data_buffers: dict[str, NumericBuffer] = {}
        self.curves: dict[str, pg.PlotDataItem] = {}

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.start_stop_button = QPushButton("Start")
        self.start_stop_button.setEnabled(False)
        self.start_stop_button.setToolTip("Start telemetry logging on the driver.")
        toolbar.addWidget(self.start_stop_button)

        self.auto_follow_button = QPushButton("Auto follow")
        self.auto_follow_button.setCheckable(True)
        self.auto_follow_button.setChecked(True)
        self.auto_follow_button.setToolTip(
            "Follow incoming data. Manual pan/zoom pauses following; click to resume."
        )
        self.auto_follow_button.toggled.connect(self._set_auto_follow)
        toolbar.addWidget(self.auto_follow_button)
        toolbar.addWidget(QLabel("Window:"))

        self.window_seconds = QDoubleSpinBox()
        self.window_seconds.setRange(
            min(0.1, max_samples / SAMPLE_RATE), max_samples / SAMPLE_RATE
        )
        self.window_seconds.setDecimals(1)
        self.window_seconds.setSingleStep(0.5)
        self.window_seconds.setValue(
            min(_DEFAULT_WINDOW_SECONDS, max_samples / SAMPLE_RATE)
        )
        self.window_seconds.setSuffix(" s")
        self.window_seconds.setToolTip(
            "Visible time span while Auto follow is on; bounded by the plot history buffer."
        )
        self.window_seconds.valueChanged.connect(self._on_window_changed)
        toolbar.addWidget(self.window_seconds)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.graph = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graph)
        self.view_box = _InteractiveViewBox()
        self.plot = self.graph.addPlot(title="Live Data", viewBox=self.view_box)
        self.view_box.userInteracted.connect(self._pause_auto_follow)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.addLegend()
        self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue_and_update)
        self.timer.start(update_interval_ms)

    def set_transport_connected(self, connected: bool) -> None:
        self.start_stop_button.setEnabled(connected)

    def set_logging_active(self, active: bool) -> None:
        self.logging_active = bool(active)
        if self.logging_active:
            self.start_stop_button.setText("Stop")
            self.start_stop_button.setToolTip("Stop telemetry logging on the driver.")
        else:
            self.start_stop_button.setText("Start")
            self.start_stop_button.setToolTip("Start telemetry logging on the driver.")

    @property
    def active_signals(self) -> tuple[str, ...]:
        """Signal names currently represented by the incoming payloads."""
        return self._signal_names

    @property
    def auto_follow_enabled(self) -> bool:
        return self._auto_follow

    def enqueue_log(self, payload: LogPayload) -> None:
        """Queue a decoded payload; discard oldest plot-only data on overload."""
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

        # Device timestamp is deliberately ignored. Establish a monotonic,
        # relative time axis from the number of received samples instead.
        first_sample = self._samples_received
        self.time_buffer.extend(
            (first_sample + sample_index) / SAMPLE_RATE
            for sample_index in range(payload.sample_count)
        )
        for name in self._signal_names:
            self.data_buffers[name].extend(payload.signals[name])
        self._samples_received += payload.sample_count

    def _reset_signal_history(self, names: tuple[str, ...]) -> None:
        """Start aligned signal history when the selected signal set changes."""
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

    def _set_auto_follow(self, enabled: bool) -> None:
        self._auto_follow = enabled
        if enabled:
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            self._update_follow_range()
        else:
            # Preserve the current view and prevent y auto-ranging from
            # overriding deliberate manual navigation.
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)

    def _pause_auto_follow(self) -> None:
        if self._auto_follow:
            self.auto_follow_button.setChecked(False)

    def _on_window_changed(self, _value: float) -> None:
        if self._auto_follow:
            self._update_follow_range()

    def _update_follow_range(self) -> None:
        if not self._auto_follow:
            return
        end_time = self._samples_received / SAMPLE_RATE
        window = self.window_seconds.value()
        start_time = max(0.0, end_time - window)
        if end_time <= start_time:
            end_time = start_time + 1.0 / SAMPLE_RATE
        self.plot.setXRange(start_time, end_time, padding=0)

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
        self._samples_received = 0
        if self._auto_follow:
            self._update_follow_range()

    def _update_plot(self) -> None:
        if not self._signal_names or not self.time_buffer:
            return

        x = np.fromiter(self.time_buffer, dtype=np.float64)
        for name in self._signal_names:
            y = np.fromiter(self.data_buffers[name], dtype=np.float64)
            if x.size != y.size:
                _LOG.error("Plot buffers lost alignment for signal %s", name)
                continue
            self.curves[name].setData(x, y)

        if self._auto_follow:
            self._update_follow_range()

    def reset_zoom(self) -> None:
        """Restore time-window auto-follow (also used by the controls dock)."""
        self.auto_follow_button.setChecked(True)
        self._update_follow_range()
