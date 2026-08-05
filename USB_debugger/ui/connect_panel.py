from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QComboBox, QPushButton, QLabel
from PySide6.QtCore import Qt

from serial_worker import SerialWorker


class SerialConnectPanel(QDockWidget):
    def __init__(self, on_connect, parent=None):
        super().__init__("Serial Connection", parent)
        self.on_connect = on_connect

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.port_combo = QComboBox()
        self.refresh_btn = QPushButton("Refresh Ports")
        self.connect_btn = QPushButton("Connect")

        layout.addWidget(QLabel("Serial Port"))
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.connect_btn)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self._connect)

        self.setWidget(panel)
        self.refresh_ports()

    def refresh_ports(self):
        self.port_combo.clear()
        ports = SerialWorker.list_available_ports()
        self.port_combo.addItems(ports)

    def _connect(self):
        port = self.port_combo.currentText().strip()
        if port:
            self.on_connect(port)