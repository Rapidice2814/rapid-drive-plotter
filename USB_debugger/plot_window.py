import queue
from collections import defaultdict, deque
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QTextEdit,
    QDockWidget,
    QLabel,
    QPushButton,
    QCheckBox,
    QScrollArea,
)
import pyqtgraph as pg

from protocol_codec import DecodedLogPayload
from protocol_definitions import FOC_USB_DEBUG_SIGNAL_LIST


class PlotWindow(QMainWindow):
    def __init__(self, on_command: Callable[[str], None]):
        super().__init__()
        self.on_command = on_command

        self.setWindowTitle("Serial Plotter")
        self.resize(1400, 900)

        self.data_buffers = defaultdict(lambda: deque(maxlen=100000))
        self.time_buffer = deque(maxlen=100000)
        self.curves = {}
        self.active_signals = []

        self._payload_queue = queue.Queue()

        self.signal_meta = [
            s for s in FOC_USB_DEBUG_SIGNAL_LIST if s["name"]
        ]
        self.signal_checkboxes = {}
        self.current_mask = 0

        self._build_ui()
        self._build_timer()
        self._update_mask_from_ui(send=False)

    def _build_ui(self):
        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)

        self.graph = pg.GraphicsLayoutWidget()
        plot_layout.addWidget(self.graph)

        self.plot = self.graph.addPlot(title="Live Data")  # type: ignore[attr-defined]
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.plot.addLegend()

        self.setCentralWidget(plot_widget)

        ################## COMMAND PANEL ##################
        self.command_dock = QDockWidget("Command Line", self)
        self.command_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        command_panel = QWidget()
        command_layout = QVBoxLayout(command_panel)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Type command and press Enter...")
        self.command_input.returnPressed.connect(self._send_command)

        self.command_log = QTextEdit()
        self.command_log.setReadOnly(True)

        command_layout.addWidget(QLabel("Command:"))
        command_layout.addWidget(self.command_input)
        command_layout.addWidget(QLabel("Output:"))
        command_layout.addWidget(self.command_log)

        self.command_dock.setWidget(command_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.command_dock)

        ################# CONTROL PANEL ##################
        self.control_dock = QDockWidget("Controls", self)
        self.control_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        control_panel = QWidget()
        control_layout = QVBoxLayout(control_panel)

        self.btn_start = QPushButton("Send Start")
        self.btn_start.clicked.connect(lambda: self._send_predefined_command("start"))
        control_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Send Stop")
        self.btn_stop.clicked.connect(lambda: self._send_predefined_command("stop"))
        control_layout.addWidget(self.btn_stop)

        
        self.btn_home = QPushButton("Reset Zoom")
        self.btn_home.clicked.connect(self._back_to_home)
        control_layout.addWidget(self.btn_home)


        self.btn_mo = QPushButton("Open loop control")
        self.btn_mo.clicked.connect(lambda: self._send_predefined_command("Mo"))
        control_layout.addWidget(self.btn_mo)
    
        self.btn_ms = QPushButton("Speed control")
        self.btn_ms.clicked.connect(lambda: self._send_predefined_command("Ms"))
        control_layout.addWidget(self.btn_ms)

        self.btn_mp = QPushButton("Position control")
        self.btn_mp.clicked.connect(lambda: self._send_predefined_command("Mp"))
        control_layout.addWidget(self.btn_mp)


        self.btn_sq0 = QPushButton("Sq0")
        self.btn_sq0.clicked.connect(lambda: self._send_predefined_command("Sq0"))
        control_layout.addWidget(self.btn_sq0)

        self.btn_sq1000 = QPushButton("Sq1000")
        self.btn_sq1000.clicked.connect(lambda: self._send_predefined_command("Sq1000"))
        control_layout.addWidget(self.btn_sq1000)

        self.btn_ss0 = QPushButton("Ss0")
        self.btn_ss0.clicked.connect(lambda: self._send_predefined_command("Ss0"))
        control_layout.addWidget(self.btn_ss0)

        self.btn_ss1 = QPushButton("Ss1")
        self.btn_ss1.clicked.connect(lambda: self._send_predefined_command("Ss1"))
        control_layout.addWidget(self.btn_ss1)

        self.btn_sp0 = QPushButton("Sp1000")
        self.btn_sp0.clicked.connect(lambda: self._send_predefined_command("Sp1000"))
        control_layout.addWidget(self.btn_sp0)

        self.btn_sp1 = QPushButton("Sp3000")
        self.btn_sp1.clicked.connect(lambda: self._send_predefined_command("Sp3000"))
        control_layout.addWidget(self.btn_sp1)



        control_layout.addStretch()

        self.control_dock.setWidget(control_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.control_dock)

        ################# SIGNAL PANEL ##################
        self.signals_dock = QDockWidget("Signals", self)
        self.signals_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        signals_panel = QWidget()
        signals_layout = QVBoxLayout(signals_panel)

        self.mask_label = QLabel("Mask: 0x00000000")
        self.mask_label.setWordWrap(True)
        signals_layout.addWidget(self.mask_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        list_container = QWidget()
        self.signals_list_layout = QVBoxLayout(list_container)

        for sig in self.signal_meta:
            bit = int(sig["bit"])
            name = sig["name"]
            cb = QCheckBox(f"bit {bit}: {name}")
            cb.setChecked(False)
            cb.stateChanged.connect(self._on_signal_toggled)
            self.signal_checkboxes[bit] = cb
            self.signals_list_layout.addWidget(cb)

        self.signals_list_layout.addStretch()
        scroll.setWidget(list_container)

        signals_layout.addWidget(scroll)
        self.signals_dock.setWidget(signals_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.signals_dock)

    def _build_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_queue_and_update)
        self.timer.start(50)

    def enqueue(self, payload: DecodedLogPayload):
        self._payload_queue.put(payload)

    def _send_command(self):
        cmd = self.command_input.text().strip()
        if not cmd:
            return
        self.on_command(cmd)
        self.command_log.append(f"> {cmd}")
        self.command_input.clear()

    def _send_predefined_command(self, cmd: str):
        self.on_command(cmd)
        self.command_log.append(f"> {cmd}")

    def _back_to_home(self):
        self.plot.autoRange()

    def _on_signal_toggled(self):
        self._update_mask_from_ui(send=True)

    def _update_mask_from_ui(self, send: bool):
        mask = 0
        for sig in self.signal_meta:
            bit = int(sig["bit"])
            cb = self.signal_checkboxes[bit]
            if cb.isChecked():
                mask |= (1 << bit)

        self.current_mask = mask
        self.mask_label.setText(f"Mask: 0x{mask:08X}")

        if send:
            self._send_predefined_command("stop")
            self._send_predefined_command(f"setmask 0x{mask:08X}")
            self._send_predefined_command("start")

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

    def _handle_log_payload(self, payload: DecodedLogPayload):
        self.time_buffer.extend(
            range(payload.timestamp, payload.timestamp + payload.sample_count)
        )

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