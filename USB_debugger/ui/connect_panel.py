from typing import Callable

from ui.busy_button import BusyButton
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt, QTimer, Signal

from serial_worker import SerialWorker
from config import (
    ENABLE_SIMULATOR,
    PORT_REFRESH_INTERVAL_SECONDS,
    SIMULATOR_ENDPOINT,
)


class SerialConnectDock(QDockWidget):
    bootloader_finished = Signal(object)

    def __init__(
        self,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        parent=None,
        on_bootloader=None,
    ):
        super().__init__("Serial Connection", parent)
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_bootloader = on_bootloader
        self.recovering = False
        self.recovery_generation = 0
        self.bootloader_finished.connect(self._bootloader_finished)
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
        self.refresh_btn = BusyButton("Refresh Ports")
        self.connect_btn = BusyButton("Connect")
        self.disconnect_btn = BusyButton("Disconnect")
        self.bootloader_btn = BusyButton("Enter bootloader")
        self.bootloader_btn.setToolTip("Use the selected serial port even if firmware version discovery fails. Stops the motor before requesting bootloader.")
        self.save_log_checkbox = QCheckBox("Save telemetry to HDF5 log file")
        self.save_log_checkbox.setChecked(False)
        self.disconnect_btn.setEnabled(False)

        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Port or simulator"))
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.disconnect_btn)
        layout.addWidget(self.bootloader_btn)
        layout.addWidget(self.save_log_checkbox)

        self.refresh_btn.activated.connect(self.refresh_ports)
        self.connect_btn.activated.connect(self._connect)
        self.disconnect_btn.activated.connect(self._disconnect)
        self.bootloader_btn.activated.connect(self._bootloader)
        self.port_combo.currentIndexChanged.connect(lambda: self._update_bootloader_button())

        self.setWidget(panel)
        self.refresh_ports()

        self.port_refresh_timer = QTimer(self)
        self.port_refresh_timer.setInterval(
            max(1, round(PORT_REFRESH_INTERVAL_SECONDS * 1000))
        )
        self.port_refresh_timer.timeout.connect(self._refresh_ports_if_disconnected)
        self.port_refresh_timer.start()

    def _selected_endpoint(self):
        endpoint = self.port_combo.currentData()
        return endpoint if endpoint is not None else self.port_combo.currentText()

    def refresh_ports(self):
        selected_endpoint = self._selected_endpoint()
        self.port_combo.clear()
        ports = SerialWorker.list_available_ports()
        for port in ports:
            self.port_combo.addItem(port, port)
        simulator_index = -1
        if ENABLE_SIMULATOR:
            simulator_index = self.port_combo.count()
            self.port_combo.addItem("Simulator (Test Mode)", SIMULATOR_ENDPOINT)

        # Keep a selected physical port when it remains available. If the
        # simulator was selected but a real port is now present, prefer the
        # first real port instead. The simulator is selected only when no
        # physical ports are available.
        selected_index = -1
        if selected_endpoint in ports:
            selected_index = ports.index(selected_endpoint)
        elif ports:
            selected_index = 0
        elif selected_endpoint == SIMULATOR_ENDPOINT:
            selected_index = simulator_index
        elif simulator_index >= 0:
            selected_index = simulator_index
        if selected_index >= 0:
            self.port_combo.setCurrentIndex(selected_index)

    def _refresh_ports_if_disconnected(self):
        if not self.connected and not self.recovering:
            self.refresh_ports()

    def set_connected(self, connected: bool, simulator: bool = False):
        self.connected = connected
        if connected or self.recovering:
            self.port_refresh_timer.stop()
        else:
            self.port_refresh_timer.start()
        if connected:
            if simulator:
                self.status_label.setText("Status: Connected (Simulator)")
                self.status_label.setStyleSheet("color: #b36b00; font-weight: bold;")
            else:
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

        if self.recovering:
            for widget in (self.connect_btn, self.disconnect_btn, self.port_combo, self.refresh_btn):
                widget.setEnabled(False)
            self.status_label.setText("Status: Requesting bootloader…")
            self.status_label.setStyleSheet("color: #b36b00; font-weight: bold;")
        self._update_bootloader_button()

    def set_bootloader_callback(self, callback):
        self.on_bootloader = callback
        self._update_bootloader_button()

    def _update_bootloader_button(self):
        endpoint = self._selected_endpoint()
        self.bootloader_btn.setEnabled(bool(endpoint) and endpoint != SIMULATOR_ENDPOINT
                                       and self.on_bootloader is not None and not self.recovering)

    def _bootloader(self):
        if not self.bootloader_btn.isEnabled():
            return
        endpoint = str(self._selected_endpoint())
        self.recovery_generation += 1
        generation = self.recovery_generation
        busy = self.bootloader_btn.begin_busy()
        self.recovering = True
        self.set_connected(self.connected)
        def done(success, message):
            try:
                self.bootloader_finished.emit((generation, busy, success, message))
            except RuntimeError:
                pass  # The window was closed while the background request finished.
        try:
            self.on_bootloader(endpoint, done)
        except (OSError, ValueError, RuntimeError) as exc:
            done(False, str(exc))

    def _bootloader_finished(self, result):
        generation, busy, success, message = result
        if generation != self.recovery_generation:
            return
        self.recovering = False
        self.bootloader_btn.end_busy(busy)
        self.set_connected(False)
        self.status_label.setText(("Status: " if success else "Bootloader request failed: ") + message)
        self.status_label.setStyleSheet(f"color: {'#b36b00' if success else 'red'}; font-weight: bold;")
        self.status_label.setWordWrap(True)

    def _connect(self):
        endpoint = self.port_combo.currentData()
        if endpoint is None:
            endpoint = self.port_combo.currentText().strip()
        if endpoint and self.on_connect is not None:
            self.on_connect(str(endpoint))

    def _disconnect(self):
        if self.on_disconnect is not None:
            self.on_disconnect()
