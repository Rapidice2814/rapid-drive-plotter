from ui.busy_button import BusyButton
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout

from protocol_codec import Packet
from protocol_definitions import MsgType


class ControlDock(QDockWidget):
    def __init__(self, on_command, on_plot_reset, parent=None):
        super().__init__("Controls", parent)
        self.on_command = on_command
        self.on_plot_reset = on_plot_reset

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.btn_start = BusyButton("Send Start")
        self.btn_start.activated.connect(
            lambda: self.on_command(Packet(msg_type=MsgType.MSG_START_LOG, data=None))
        )
        layout.addWidget(self.btn_start)

        self.btn_stop = BusyButton("Send Stop")
        self.btn_stop.activated.connect(
            lambda: self.on_command(Packet(msg_type=MsgType.MSG_STOP_LOG, data=None))
        )
        layout.addWidget(self.btn_stop)

        self.btn_home = BusyButton("Auto follow / reset zoom")
        self.btn_home.activated.connect(self.on_plot_reset)
        layout.addWidget(self.btn_home)

        layout.addStretch()
        self.setWidget(panel)
