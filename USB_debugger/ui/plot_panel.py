import queue
from collections import defaultdict, deque

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import config
from protocol_codec import LogPayload


class PlotPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.data_buffers = defaultdict(lambda: deque(maxlen=100000))
        self.time_buffer = deque(maxlen=100000)
        self.curves = {}
        self.active_signals = []
        self._payload_queue = queue.Queue()

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Trace view:"))
        self.auto_follow_checkbox = QCheckBox("Auto-follow latest data")
        self.auto_follow_checkbox.setChecked(True)
        self.auto_follow_checkbox.toggled.connect(self._set_auto_follow)
        toolbar.addWidget(self.auto_follow_checkbox)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.graph = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graph)

        self.plot = self.graph.addPlot(title="Live Data")  # type: ignore[attr-defined]
        self._programmatic_range_change = False
        manual_range_signal = getattr(self.plot.getViewBox(), "sigRangeChangedManually", None)
        if manual_range_signal is not None:
            manual_range_signal.connect(self._on_manual_range_change)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.addLegend()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue_and_update)
        self.timer.start(50)

    def enqueue_log(self, payload: LogPayload):
        self._payload_queue.put(payload)

    def _set_auto_follow(self, enabled: bool):
        if enabled:
            self._auto_follow_latest()

    def _on_manual_range_change(self, _ranges):
        # Let the user inspect history without the next telemetry frame
        # immediately snapping the view back.  The checkbox at the top is the
        # explicit way to return to live-follow mode.
        if not self._programmatic_range_change and self.auto_follow_checkbox.isChecked():
            self.auto_follow_checkbox.setChecked(False)

    def _auto_follow_latest(self):
        if not self.time_buffer:
            self.plot.autoRange()
            return

        newest = self.time_buffer[-1]
        oldest = self.time_buffer[0]
        window = max(float(config.AUTO_FOLLOW_WINDOW_S), 1.0 / config.LOG_FREQ)
        left = max(oldest, newest - window)
        self._programmatic_range_change = True
        try:
            self.plot.enableAutoRange(x=False, y=True)
            self.plot.setXRange(left, newest, padding=0)
        finally:
            self._programmatic_range_change = False

    def _drain_queue_and_update(self):
        updated = False
        while True:
            try:
                payload = self._payload_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_log_payload(payload)
            updated = True

        if updated:
            self._update_plot()

    def _handle_log_payload(self, payload: LogPayload):
        start_t = payload.timestamp / config.LOG_FREQ
        LOG_DT = 1.0 / config.LOG_FREQ
        self.time_buffer.extend(start_t + i * LOG_DT for i in range(payload.sample_count))

        for name, values in payload.signals.items():
            self.data_buffers[name].extend(values)

        self.active_signals = list(payload.signals.keys())
        self._refresh_curves()

    def _refresh_curves(self):
        for name in self.active_signals:
            if name not in self.curves:
                self.curves[name] = self.plot.plot(
                    pen=pg.intColor(len(self.curves)),
                    name=name,
                )

        for name in list(self.curves.keys()):
            if name not in self.active_signals:
                self.plot.removeItem(self.curves[name])
                del self.curves[name]

    def _update_plot(self):
        if not self.active_signals or not self.time_buffer:
            return

        x = list(self.time_buffer)
        for name in self.active_signals:
            y = list(self.data_buffers[name])
            n = min(len(x), len(y))
            if n > 0 and name in self.curves:
                self.curves[name].setData(x[-n:], y[-n:])

        if self.auto_follow_checkbox.isChecked():
            self._auto_follow_latest()

    def reset_zoom(self):
        self.auto_follow_checkbox.setChecked(True)
        self._auto_follow_latest()
