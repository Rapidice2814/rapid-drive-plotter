from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from protocol_codec import LogPayload, PIDPayload, Packet, TextPayload, VarPayload
from protocol_definitions import MsgType

from serial_worker import SerialWorker
from ui.command_panel import CommandDock
from ui.pid_panel import PidDock
from ui.signal_selector_panel import SignalSelectorDock
from ui.plot_panel import PlotPanel
from ui.control_panel import ControlDock
from ui.variable_panel import VarDock
from ui.connect_panel import SerialConnectDock


class PlotWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.on_command: Callable[[Packet], None] = lambda _pkt: None
        self.start_serial_callback: Callable[[str], None] | None = None
        self.worker: SerialWorker | None = None

        self.setWindowTitle("Serial Plotter")
        self.resize(1400, 900)

        self._build_ui()

    def set_command_sender(self, sender: Callable[[Packet], None]):
        self.on_command = sender
        self._refresh_command_targets()

    def set_start_serial_callback(self, callback: Callable[[str], None]):
        self.start_serial_callback = callback
        if hasattr(self, "connect_dock"):
            self.connect_dock.on_connect = callback

    def _refresh_command_targets(self):
        if hasattr(self, "signal_dock"):
            self.signal_dock.on_command = self.on_command
        if hasattr(self, "pid_dock"):
            self.pid_dock.on_command = self.on_command
        if hasattr(self, "control_dock"):
            self.control_dock.on_command = self.on_command
        if hasattr(self, "var_dock"):
            self.var_dock.on_command = self.on_command

    def _build_ui(self):
        self.plot_panel = PlotPanel(self)
        self.setCentralWidget(self.plot_panel)

        self.command_dock = CommandDock(self._handle_text_command, self)

        self.connect_dock = SerialConnectDock(
            self._on_connect_clicked,
            self._on_disconnect_clicked,
            self,
        )

        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.command_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.connect_dock)
        self.splitDockWidget(self.command_dock, self.connect_dock, Qt.Orientation.Horizontal)

        self.resizeDocks(
            [self.command_dock, self.connect_dock],
            [8, 1],
            Qt.Orientation.Horizontal,
        )


        self.signal_dock = SignalSelectorDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.signal_dock)

        self.pid_dock = PidDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.pid_dock)

        self.control_dock = ControlDock(
            on_command=self.on_command,
            on_plot_reset=self._back_to_home,
            parent=self,
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.control_dock)

        self.var_dock = VarDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.var_dock)

        self.tabifyDockWidget(self.control_dock, self.pid_dock)
        self.tabifyDockWidget(self.control_dock, self.var_dock)
        self.control_dock.raise_()

    def _on_connect_clicked(self, port: str):
        if self.worker is not None:
            return

        if self.start_serial_callback is not None:
            self.start_serial_callback(port)
        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(True)

    def _on_disconnect_clicked(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(False)

    def set_worker(self, worker: SerialWorker | None):
        self.worker = worker
        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(worker is not None)

    def enqueue_log(self, payload: LogPayload):
        self.plot_panel.enqueue_log(payload)

    def enqueue_text(self, payload: str):
        self.command_dock.enqueue_text(payload)

    def on_reply(self, packet: Packet):
        if packet.msg_type == MsgType.MSG_TEXT_REPLY and isinstance(packet.data, TextPayload):
            self.enqueue_text(packet.data.text)

        elif packet.msg_type == MsgType.MSG_PID_REPLY and isinstance(packet.data, PIDPayload):
            if packet.data.kp is not None and packet.data.ki is not None and packet.data.kd is not None:
                self.enqueue_text(str(packet.data))
                self.pid_dock.set_pid_values(
                    packet.data.controller_id,
                    packet.data.kp,
                    packet.data.ki,
                    packet.data.kd,
                )

        elif packet.msg_type == MsgType.MSG_VAR_REPLY and isinstance(packet.data, VarPayload):
            if packet.data.value is not None:
                self.enqueue_text(str(packet.data))
                self.var_dock.set_var_value(packet.data.var_id, float(packet.data.value))

    def _handle_text_command(self, cmd: str):
        cmd = cmd.strip()
        if not cmd:
            return

        text_payload = TextPayload(text=cmd)
        pkg = Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=text_payload)
        self.on_command(pkg)

    def _back_to_home(self):
        self.plot_panel.reset_zoom()