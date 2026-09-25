from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import STATE_POLL_INTERVAL_SECONDS
from protocol_codec import Packet
from protocol_definitions import FOCState, MsgType


class BasicsDock(QDockWidget):
    """Displays firmware identity and periodically polled driver state."""

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

        version_group = QGroupBox("Firmware")
        version_form = QFormLayout(version_group)
        self.version_label = QLabel("Not connected")
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        version_form.addRow("Version", self.version_label)
        self.version_button = QPushButton("Read version")
        self.version_button.clicked.connect(self.request_version)
        version_form.addRow(self.version_button)
        layout.addWidget(version_group)

        state_group = QGroupBox("Driver state")
        state_form = QFormLayout(state_group)
        self.state_label = QLabel("Not connected")
        self.state_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_form.addRow("Current state", self.state_label)
        self.state_button = QPushButton("Read state now")
        self.state_button.clicked.connect(self.request_state)
        state_form.addRow(self.state_button)
        self.polling_label = QLabel(
            f"Automatic refresh every {STATE_POLL_INTERVAL_SECONDS:g} s"
        )
        self.polling_label.setWordWrap(True)
        state_form.addRow(self.polling_label)
        layout.addWidget(state_group)

        self.stop_state_button = QPushButton("Enter STOP state")
        self.stop_state_button.setToolTip("Set the driver state to FOC_STATE_STOP.")
        self.stop_state_button.clicked.connect(
            lambda: self._send_state(FOCState.FOC_STATE_STOP)
        )
        layout.addWidget(self.stop_state_button)

        state_select_group = QGroupBox("Go to a specific state")
        state_select_layout = QVBoxLayout(state_select_group)
        self.state_combo = QComboBox()
        for state in FOCState:
            if state == FOCState.FOC_STATE_COUNT:
                continue
            label = state.name.removeprefix("FOC_STATE_").replace("_", " ").title()
            self.state_combo.addItem(f"{label} ({state.value})", state.value)
        state_select_layout.addWidget(self.state_combo)
        self.go_to_state_button = QPushButton("Go to selected state")
        self.go_to_state_button.clicked.connect(self._send_selected_state)
        state_select_layout.addWidget(self.go_to_state_button)
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
        flash_layout.addWidget(self.flash_save_button)
        flash_layout.addWidget(self.flash_load_button)
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
        self.state_timer = QTimer(self)
        self.state_timer.setInterval(max(1, round(STATE_POLL_INTERVAL_SECONDS * 1000)))
        self.state_timer.timeout.connect(self.request_state)
        self._set_command_buttons_enabled(False)

    def _send(self, msg_type: MsgType):
        if self._connected:
            self.on_command(Packet(msg_type=msg_type, data=None))

    def begin_connection(self):
        self.state_timer.stop()
        self._connected = False
        self.version_label.setText("Requesting…")
        self.state_label.setText("Waiting for driver…")
        self._set_command_buttons_enabled(False)

    def transport_ready(self):
        self._connected = True
        self._set_command_buttons_enabled(True)
        self.request_state()
        self.state_timer.start()

    def set_disconnected(self):
        self.state_timer.stop()
        self._connected = False
        self.state_label.setText("Disconnected")
        self._set_command_buttons_enabled(False)

    def _set_command_buttons_enabled(self, enabled: bool):
        for button in (
            self.version_button,
            self.state_button,
            self.flash_save_button,
            self.flash_load_button,
            self.enter_bootloader_button,
            self.stop_state_button,
            self.state_combo,
            self.go_to_state_button,
        ):
            button.setEnabled(enabled)

    def request_version(self):
        self._send(MsgType.MSG_GET_VERSION)

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
        self.version_label.setText(f"{major}.{minor}.{patch}")

    def set_state(self, state_value: int):
        try:
            state = FOCState(state_value)
            if state == FOCState.FOC_STATE_COUNT:
                raise ValueError("FOC_STATE_COUNT is not a runtime state")
            state_name = state.name.removeprefix("FOC_STATE_").replace("_", " ")
            self.state_label.setText(f"{state_value}  {state_name}")
        except ValueError:
            self.state_label.setText(f"{state_value}  Unknown state")
