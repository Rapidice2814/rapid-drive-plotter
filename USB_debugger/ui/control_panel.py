from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QPushButton

from protocol_codec import Packet, TextPayload
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

        self.btn_start = QPushButton("Send Start")
        self.btn_start.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_START_LOG, data=None)))
        layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Send Stop")
        self.btn_stop.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_STOP_LOG, data=None)))
        layout.addWidget(self.btn_stop)

        self.btn_home = QPushButton("Reset Zoom")
        self.btn_home.clicked.connect(self.on_plot_reset)
        layout.addWidget(self.btn_home)

        self.btn_mo = QPushButton("Open loop control")
        self.btn_mo.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Mo"))))
        layout.addWidget(self.btn_mo)

        self.btn_ms = QPushButton("Speed control")
        self.btn_ms.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Ms"))))
        layout.addWidget(self.btn_ms)

        self.btn_mp = QPushButton("Position control")
        self.btn_mp.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Mp"))))
        layout.addWidget(self.btn_mp)

        self.btn_sq0 = QPushButton("Sq0")
        self.btn_sq0.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sq0"))))
        layout.addWidget(self.btn_sq0)

        self.btn_sq1000 = QPushButton("Sq1000")
        self.btn_sq1000.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sq1000"))))
        layout.addWidget(self.btn_sq1000)

        self.btn_ss0 = QPushButton("Ss0")
        self.btn_ss0.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Ss0"))))
        layout.addWidget(self.btn_ss0)

        self.btn_ss1 = QPushButton("Ss1")
        self.btn_ss1.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Ss1"))))
        layout.addWidget(self.btn_ss1)

        self.btn_sp0 = QPushButton("Sp0")
        self.btn_sp0.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sp0"))))
        layout.addWidget(self.btn_sp0)

        self.btn_sp1 = QPushButton("Sp3100")
        self.btn_sp1.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sp3100"))))
        layout.addWidget(self.btn_sp1)

        self.btn_sp2 = QPushButton("Sp5100")
        self.btn_sp2.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sp5100"))))
        layout.addWidget(self.btn_sp2)

        self.btn_sp3 = QPushButton("Sp6200")
        self.btn_sp3.clicked.connect(lambda: self.on_command(Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=TextPayload(text="Sp6200"))))
        layout.addWidget(self.btn_sp3)

        layout.addStretch()
        self.setWidget(panel)