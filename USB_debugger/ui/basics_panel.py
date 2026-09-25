from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import (
    STATE_POLL_INTERVAL_SECONDS,
    VERSION_POLL_INTERVAL_SECONDS,
    VERSION_REPLY_TIMEOUT_SECONDS,
)
from protocol_codec import Packet
from protocol_definitions import FOCState, MsgType


class BasicsDock(QDockWidget):
    """Shows driver health, current state, and firmware identity."""

    def __init__(self, on_command, parent=None):
        super().__init__("Basics", parent)
        self.on_command = on_command
        self._connected = False

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        status_group = QGroupBox("Connection status")
        status_layout = QHBoxLayout(status_group)
        self.driver_status_prefix_label = QLabel("Status")
        self.driver_status_label = QLabel("ERROR")
        self.driver_status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.driver_status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._set_driver_status("ERROR", "error")
        status_layout.addWidget(self.driver_status_prefix_label)
        status_layout.addWidget(self.driver_status_label)
        status_layout.addStretch()
        layout.addWidget(status_group)

        state_group = QGroupBox("Driver state")
        state_form = QFormLayout(state_group)
        self.state_label = QLabel("Not connected")
        self.state_label.setStyleSheet("color: #1976d2; font-weight: bold;")
        self.state_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.state_button = QPushButton("Read state")
        self.state_button.clicked.connect(self.request_state)
        state_inline = QWidget()
        state_inline_layout = QHBoxLayout(state_inline)
        state_inline_layout.setContentsMargins(0, 0, 0, 0)
        state_inline_layout.addWidget(self.state_label)
        state_inline_layout.addStretch()
        state_inline_layout.addWidget(self.state_button)
        state_form.addRow("Current state", state_inline)
        layout.addWidget(state_group)

        version_group = QGroupBox("Firmware")
        version_form = QFormLayout(version_group)
        self.version_label = QLabel("Not connected")
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.version_button = QPushButton("Read version")
        self.version_button.clicked.connect(self.request_version)
        version_inline = QWidget()
        version_inline_layout = QHBoxLayout(version_inline)
        version_inline_layout.setContentsMargins(0, 0, 0, 0)
        version_inline_layout.addWidget(self.version_label)
        version_inline_layout.addStretch()
        version_inline_layout.addWidget(self.version_button)
        version_form.addRow("Version", version_inline)
        layout.addWidget(version_group)

        state_select_group = QGroupBox("Go to a specific state")
        state_select_layout = QVBoxLayout(state_select_group)
        self.stop_state_button = QPushButton("Enter STOP state")
        self.stop_state_button.setToolTip("Set the driver state to FOC_STATE_STOP.")
        self.stop_state_button.clicked.connect(
            lambda: self._send_state(FOCState.FOC_STATE_STOP)
        )
        state_select_layout.addWidget(self.stop_state_button)
        self.run_state_button = QPushButton("Enter RUN state")
        self.run_state_button.setToolTip("Set the driver state to FOC_STATE_RUN.")
        self.run_state_button.clicked.connect(
            lambda: self._send_state(FOCState.FOC_STATE_RUN)
        )
        state_select_layout.addWidget(self.run_state_button)

        selected_state_row = QHBoxLayout()
        self.state_combo = QComboBox()
        for state in FOCState:
            if state == FOCState.FOC_STATE_COUNT:
                continue
            label = state.name.removeprefix("FOC_STATE_").replace("_", " ").title()
            self.state_combo.addItem(f"{label} ({state.value})", state.value)
        selected_state_row.addWidget(self.state_combo, 1)
        self.go_to_state_button = QPushButton("Go to selected state")
        self.go_to_state_button.clicked.connect(self._send_selected_state)
        selected_state_row.addWidget(self.go_to_state_button)
        state_select_layout.addLayout(selected_state_row)
        layout.addWidget(state_select_group)

        flash_group = QGroupBox("Flash storage")
        flash_layout = QVBoxLayout(flash_group)
        self.flash_save_button = QPushButton("Save settings to flash")
        self.flash_save_button.setToolTip("Send the FLASH_SAVE command to the driver.")
        self.flash_save_button.clicked.connect(
            lambda: self._send(MsgType.MSG_FLASH_SAVE)
        )
        self.flash_load_button = QPushButton("Load settings from flash")
        self.flash_load_button.setToolTip("Send the FLASH_LOAD command to the driver.")
        self.flash_load_button.clicked.connect(
            lambda: self._send(MsgType.MSG_FLASH_LOAD)
        )
        self.flash_clear_button = QPushButton("Clear settings from flash")
        self.flash_clear_button.setToolTip(
            "Send the MSG_FLASH_CLEAR command to clear saved settings."
        )
        self.flash_clear_button.clicked.connect(
            lambda: self._send(MsgType.MSG_FLASH_CLEAR)
        )
        flash_layout.addWidget(self.flash_save_button)
        flash_layout.addWidget(self.flash_load_button)
        flash_layout.addWidget(self.flash_clear_button)
        layout.addWidget(flash_group)

        bootloader_group = QGroupBox("Bootloader")
        bootloader_layout = QVBoxLayout(bootloader_group)
        self.enter_bootloader_button = QPushButton("Enter bootloader")
        self.enter_bootloader_button.setToolTip(
            "Send the MSG_ENDER_BOOTLOADER command to the driver."
        )
        self.enter_bootloader_button.clicked.connect(
            lambda: self._send(MsgType.MSG_ENDER_BOOTLOADER)
        )
        bootloader_layout.addWidget(self.enter_bootloader_button)
        layout.addWidget(bootloader_group)
        layout.addStretch()
        self.setWidget(panel)

        self.version_poll_timer = QTimer(self)
        self.version_poll_timer.setInterval(
            max(1, round(VERSION_POLL_INTERVAL_SECONDS * 1000))
        )
        self.version_poll_timer.timeout.connect(self.request_version)

        self.state_poll_timer = QTimer(self)
        self.state_poll_timer.setInterval(
            max(1, round(STATE_POLL_INTERVAL_SECONDS * 1000))
        )
        self.state_poll_timer.timeout.connect(self.request_state)

        self.version_reply_timeout_timer = QTimer(self)
        self.version_reply_timeout_timer.setSingleShot(True)
        self.version_reply_timeout_timer.setInterval(
            max(1, round(VERSION_REPLY_TIMEOUT_SECONDS * 1000))
        )
        self.version_reply_timeout_timer.timeout.connect(self._on_version_timeout)
        self._set_command_buttons_enabled(False)

    def _set_driver_status(self, text: str, status: str):
        colors = {"ok": "#16803c", "error": "#c62828"}
        self.driver_status_label.setText(text)
        self.driver_status_label.setStyleSheet(
            f"color: {colors.get(status, colors['error'])}; font-weight: bold;"
        )

    def _send(self, msg_type: MsgType):
        if self._connected:
            self.on_command(Packet(msg_type=msg_type, data=None))

    def begin_connection(self):
        self.version_poll_timer.stop()
        self.state_poll_timer.stop()
        self.version_reply_timeout_timer.stop()
        self._connected = False
        self.driver_status_label.setToolTip("")
        self._set_driver_status("ERROR", "error")
        self.version_label.setText("Waiting for reply…")
        self.state_label.setText("Waiting for driver…")
        self._set_command_buttons_enabled(False)

    def transport_ready(self):
        self._connected = True
        self._set_command_buttons_enabled(True)
        self.request_version()
        self.request_state()
        self.version_poll_timer.start()
        self.state_poll_timer.start()

    def set_disconnected(self):
        self.version_poll_timer.stop()
        self.state_poll_timer.stop()
        self.version_reply_timeout_timer.stop()
        self._connected = False
        self.driver_status_label.setToolTip("")
        self._set_driver_status("ERROR", "error")
        self.state_label.setText("Disconnected")
        self._set_command_buttons_enabled(False)

    def _set_command_buttons_enabled(self, enabled: bool):
        for button in (
            self.version_button,
            self.state_button,
            self.flash_save_button,
            self.flash_load_button,
            self.flash_clear_button,
            self.enter_bootloader_button,
            self.stop_state_button,
            self.run_state_button,
            self.state_combo,
            self.go_to_state_button,
        ):
            button.setEnabled(enabled)

    def request_version(self):
        if not self._connected or self.version_reply_timeout_timer.isActive():
            return
        self.on_command(Packet(msg_type=MsgType.MSG_GET_VERSION, data=None))
        self.version_reply_timeout_timer.start()

    def _on_version_timeout(self):
        if self._connected:
            self._set_driver_status("ERROR", "error")
            self.driver_status_label.setToolTip(
                "No valid VERSION_REPLY was received before the timeout."
            )

    def request_state(self):
        self._send(MsgType.MSG_GET_STATE)

    def _send_state(self, state: FOCState):
        if self._connected:
            self.on_command(
                Packet(msg_type=MsgType.MSG_SET_STATE, data=bytes((int(state),)))
            )

    def _send_selected_state(self):
        state_value = self.state_combo.currentData()
        if state_value is not None:
            self._send_state(FOCState(int(state_value)))

    def set_version(self, major: int, minor: int, patch: int):
        self.version_reply_timeout_timer.stop()
        self.version_label.setText(f"{major}.{minor}.{patch}")
        if self._connected:
            self.driver_status_label.setToolTip("")
            self._set_driver_status("OK", "ok")

    def set_state(self, state_value: int):
        if not self._connected:
            return
        try:
            state = FOCState(state_value)
            if state == FOCState.FOC_STATE_COUNT:
                raise ValueError("FOC_STATE_COUNT is not a runtime state")
            state_name = state.name.removeprefix("FOC_STATE_").replace("_", " ")
            self.state_label.setText(state_name)
        except ValueError:
            self.state_label.setText("Unknown state")
