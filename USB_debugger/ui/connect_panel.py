from typing import Callable

from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QComboBox, QPushButton, QLabel
from PySide6.QtCore import Qt

from serial_worker import SerialWorker


class SerialConnectDock(QDockWidget):
    def __init__(
        self,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__("Serial Connection", parent)
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.connected = False

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.status_label = QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")

        self.port_combo = QComboBox()
        self.refresh_btn = QPushButton("Refresh Ports")
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)

        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Serial Port"))
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.disconnect_btn)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self._connect)
        self.disconnect_btn.clicked.connect(self._disconnect)

        self.setWidget(panel)
        self.refresh_ports()

    def refresh_ports(self):
        self.port_combo.clear()
        ports = SerialWorker.list_available_ports()
        self.port_combo.addItems(ports)

    def set_connected(self, connected: bool):
        self.connected = connected
        if connected:
            self.status_label.setText("Status: Connected")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.port_combo.setEnabled(False)
            self.refresh_btn.setEnabled(False)
        else:
            self.status_label.setText("Status: Disconnected")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.port_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)

    def _connect(self):
        port = self.port_combo.currentText().strip()
        if port and self.on_connect is not None:
            self.on_connect(port)

    def _disconnect(self):
        if self.on_disconnect is not None:
            self.on_disconnect()