import queue
from collections import defaultdict, deque


import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout


from protocol_codec import LogPayload


LOG_FREQ = 8000
LOG_DT = 1.0 / LOG_FREQ


class PlotPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.data_buffers = defaultdict(lambda: deque(maxlen=100000))
        self.time_buffer = deque(maxlen=100000)
        self.curves = {}
        self.active_signals = []

        self._payload_queue = queue.Queue()

        layout = QVBoxLayout(self)

        self.graph = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graph)

        self.plot = self.graph.addPlot(title="Live Data")  # type: ignore[attr-defined]
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.addLegend()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue_and_update)
        self.timer.start(50)

    def enqueue_log(self, payload: LogPayload):
        self._payload_queue.put(payload)

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
        start_t = payload.timestamp / LOG_FREQ
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

    def reset_zoom(self):
        self.plot.autoRange()