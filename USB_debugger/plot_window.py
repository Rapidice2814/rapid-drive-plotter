import queue
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from protocol_codec import LogPayload, PIDPayload, Packet, TextPayload, VarPayload
from protocol_definitions import MsgType

from ui.command_panel import CommandDock
from ui.pid_panel import PidDock
from ui.signal_selector_panel import SignalSelectorDock
from ui.plot_panel import PlotPanel
from ui.control_panel import ControlDock
from ui.variable_panel import VarDock
from ui.connect_panel import SerialConnectPanel

class PlotWindow(QMainWindow):
    def __init__(self, on_command: Callable[[Packet], None]):
        super().__init__()
        self.on_command = on_command

        self.setWindowTitle("Serial Plotter")
        self.resize(1400, 900)

        self._build_ui()

    def _build_ui(self):
        self.plot_panel = PlotPanel(self)
        self.setCentralWidget(self.plot_panel)

        self.command_dock = CommandDock(self._handle_text_command, self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.command_dock)

        # self.connect_dock = SerialConnectPanel(self.start_serial, self)
        # self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.connect_dock)

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

        # if cmd.lower() == "3":
        #     self.on_command(Packet(msg_type=MsgType.MSG_SET_MASK, data=0x080000EF))
        #     return

        # if cmd.lower() == "4":
        #     self.on_command(Packet(msg_type=MsgType.MSG_SET_MASK, data=0x000000EF))
        #     return

        # if cmd.lower() == "12":
        #     pkg = Packet(msg_type=MsgType.MSG_SET_VAR, data=VarPayload(var_id=4, value=float(2.0)))
        #     self.on_command(pkg)
        #     return

        # if cmd.lower() == "13":
        #     pkg = Packet(msg_type=MsgType.MSG_SET_VAR, data=VarPayload(var_id=4, value=float(5.0)))
        #     self.on_command(pkg)
        #     return

        # if cmd.lower() == "10":
        #     pkg = Packet(msg_type=MsgType.MSG_GET_VAR, data=VarPayload(var_id=4, value=None))
        #     self.on_command(pkg)
        #     return

        text_payload = TextPayload(text=cmd)
        pkg = Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=text_payload)
        self.on_command(pkg)

    def _back_to_home(self):
        self.plot_panel.reset_zoom()