from ui.busy_button import BusyButton
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QMessageBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config import (
    CONTROL_MODE_POLL_INTERVAL_SECONDS,
    ERROR_POLL_INTERVAL_SECONDS,
    STATE_POLL_INTERVAL_SECONDS,
    VERSION_POLL_INTERVAL_SECONDS,
    VERSION_REPLY_TIMEOUT_SECONDS,
)
from protocol_codec import (ControlModePayload, Packet, StatePayload, VarPayload,
                            EncoderInfoPayload, EncoderSourcePayload, EncoderCorrectionPayload)
from protocol_definitions import CONTROL_MODE_LABELS, FOC_ERROR_LABELS, ControlMode, FOCState, MsgType


class BasicsDock(QDockWidget):
    """Shows driver health, current state, and firmware identity."""

    def __init__(self, on_command, parent=None):
        super().__init__("Basics", parent)
        self.on_command = on_command
        self.variable_ids = {}
        self._connected = False
        self._current_state: FOCState | None = None
        self._current_control_mode: ControlMode | None = None
        self._encoder_info: EncoderInfoPayload | None = None
        self._calibration_transfer_busy = False

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

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
        self.state_button = BusyButton("Read state")
        self.state_button.activated.connect(self.request_state)
        state_inline = QWidget()
        state_inline_layout = QHBoxLayout(state_inline)
        state_inline_layout.setContentsMargins(0, 0, 0, 0)
        state_inline_layout.addWidget(self.state_label)
        state_inline_layout.addStretch()
        state_inline_layout.addWidget(self.state_button)
        state_form.addRow("Current state", state_inline)
        self.control_mode_label = QLabel("Not available outside RUN")
        self.control_mode_label.setStyleSheet("color: #1976d2; font-weight: bold;")
        self.control_mode_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_form.addRow("Current mode", self.control_mode_label)
        layout.addWidget(state_group)

        errors_group = QGroupBox("Driver errors")
        errors_form = QFormLayout(errors_group)
        self.active_errors_label = QLabel("Not read")
        self.active_errors_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.active_errors_label.setWordWrap(True)
        self._style_error_label(self.active_errors_label, None)
        active_row = QWidget()
        active_row_layout = QHBoxLayout(active_row)
        active_row_layout.setContentsMargins(0, 0, 0, 0)
        active_row_layout.addWidget(self.active_errors_label, 1)
        self.read_active_errors_button = BusyButton("Read")
        self.read_active_errors_button.activated.connect(self.request_active_errors)
        active_row_layout.addWidget(self.read_active_errors_button)
        errors_form.addRow("Active", active_row)

        self.latched_errors_label = QLabel("Not read")
        self.latched_errors_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.latched_errors_label.setWordWrap(True)
        self._style_error_label(self.latched_errors_label, None)
        latched_row = QWidget()
        latched_row_layout = QHBoxLayout(latched_row)
        latched_row_layout.setContentsMargins(0, 0, 0, 0)
        latched_row_layout.addWidget(self.latched_errors_label, 1)
        self.read_latched_errors_button = BusyButton("Read")
        self.read_latched_errors_button.activated.connect(self.request_latched_errors)
        latched_row_layout.addWidget(self.read_latched_errors_button)
        errors_form.addRow("Latched", latched_row)

        self.clear_latched_errors_button = BusyButton("Clear latched errors")
        self.clear_latched_errors_button.setToolTip(
            "Clear latched driver errors in any state."
        )
        self.clear_latched_errors_button.activated.connect(self.clear_latched_errors)
        errors_form.addRow(self.clear_latched_errors_button)
        layout.addWidget(errors_group)

        version_group = QGroupBox("Firmware")
        version_form = QFormLayout(version_group)
        self.version_label = QLabel("Not connected")
        self.version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.version_button = BusyButton("Read version")
        self.version_button.activated.connect(self.request_version)
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
        self.stop_state_button = BusyButton("Enter STOP state")
        self.stop_state_button.setToolTip("Set the driver state to FOC_STATE_STOP.")
        self.stop_state_button.activated.connect(
            lambda: self._send_state(FOCState.FOC_STATE_STOP)
        )
        state_select_layout.addWidget(self.stop_state_button)
        self.run_state_button = BusyButton("Enter RUN state")
        self.run_state_button.setToolTip("Set the driver state to FOC_STATE_RUN.")
        self.run_state_button.activated.connect(
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
        self.go_to_state_button = BusyButton("Go to selected state")
        self.go_to_state_button.activated.connect(self._send_selected_state)
        selected_state_row.addWidget(self.go_to_state_button)
        state_select_layout.addLayout(selected_state_row)
        layout.addWidget(state_select_group)

        control_mode_group = QGroupBox("Go to a specific control mode")
        mode_select_layout = QHBoxLayout(control_mode_group)
        self.control_mode_combo = QComboBox()
        for mode, label in CONTROL_MODE_LABELS.items():
            self.control_mode_combo.addItem(label, int(mode))
        mode_select_layout.addWidget(self.control_mode_combo, 1)
        self.set_control_mode_button = BusyButton("Go to selected mode")
        self.set_control_mode_button.activated.connect(self.set_selected_control_mode)
        mode_select_layout.addWidget(self.set_control_mode_button)
        layout.addWidget(control_mode_group)

        self._build_encoder_controls(layout)

        flash_group = QGroupBox("Flash storage")
        flash_layout = QVBoxLayout(flash_group)
        self.flash_save_button = BusyButton("Save settings to flash")
        self.flash_save_button.setToolTip(
            "Send FLASH_SAVE. Available in IDLE or ERROR; faults are not cleared."
        )
        self.flash_save_button.activated.connect(
            lambda: self._send(MsgType.MSG_FLASH_SAVE)
        )
        self.flash_load_button = BusyButton("Load settings from flash")
        self.flash_load_button.setToolTip(
            "Send FLASH_LOAD. Available in IDLE or ERROR; faults are not cleared."
        )
        self.flash_load_button.activated.connect(
            lambda: self._send(MsgType.MSG_FLASH_LOAD)
        )
        self.flash_clear_button = BusyButton("Clear settings from flash")
        self.flash_clear_button.setToolTip(
            "Send FLASH_CLEAR. Available in IDLE or ERROR; faults are not cleared."
        )
        self.flash_clear_button.activated.connect(
            lambda: self._send(MsgType.MSG_FLASH_CLEAR)
        )
        flash_layout.addWidget(self.flash_save_button)
        flash_layout.addWidget(self.flash_load_button)
        flash_layout.addWidget(self.flash_clear_button)
        layout.addWidget(flash_group)

        bootloader_group = QGroupBox("Bootloader")
        bootloader_layout = QVBoxLayout(bootloader_group)
        self.enter_bootloader_button = BusyButton("Enter bootloader")
        self.enter_bootloader_button.setToolTip(
            "Available in IDLE or ERROR. Motor outputs remain disabled."
        )
        self.enter_bootloader_button.activated.connect(
            lambda: self._send(MsgType.MSG_ENTER_BOOTLOADER)
        )
        bootloader_layout.addWidget(self.enter_bootloader_button)
        layout.addWidget(bootloader_group)
        layout.addStretch()
        # Keep all controls inside the dock when its contents exceed its height.
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setWidget(panel)
        self.setWidget(self.scroll_area)

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
        self.state_poll_timer.timeout.connect(self.request_encoder_info)

        self.error_poll_timer = QTimer(self)
        self._error_poll_interval_ms = max(
            0, round(ERROR_POLL_INTERVAL_SECONDS * 1000)
        )
        self.error_poll_timer.setInterval(max(1, self._error_poll_interval_ms))
        self.error_poll_timer.timeout.connect(self.request_errors)

        self.control_mode_poll_timer = QTimer(self)
        self._control_mode_poll_interval_ms = max(
            0, round(CONTROL_MODE_POLL_INTERVAL_SECONDS * 1000)
        )
        self.control_mode_poll_timer.setInterval(
            max(1, self._control_mode_poll_interval_ms)
        )
        self.control_mode_poll_timer.timeout.connect(self.request_control_mode)

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
        self._clear_encoder_info()
        self.version_poll_timer.stop()
        self.state_poll_timer.stop()
        self.error_poll_timer.stop()
        self.control_mode_poll_timer.stop()
        self.version_reply_timeout_timer.stop()
        self._connected = False
        self._current_state = None
        self._current_control_mode = None
        self.control_mode_label.setText("Waiting for driver state…")
        self.driver_status_label.setToolTip("")
        self._set_driver_status("ERROR", "error")
        self.version_label.setText("Waiting for reply…")
        self.state_label.setText("Waiting for driver…")
        self._show_error_value(self.active_errors_label, "Waiting for reply")
        self._show_error_value(self.latched_errors_label, "Waiting for reply")
        self._set_command_buttons_enabled(False)

    def transport_ready(self):
        self._connected = True
        self._set_command_buttons_enabled(True)
        self.request_version()
        self.request_state()
        self.request_encoder_info()
        self.version_poll_timer.start()
        self.state_poll_timer.start()
        self.request_errors()
        if self._error_poll_interval_ms > 0:
            self.error_poll_timer.start()

    def set_disconnected(self):
        self._clear_encoder_info()
        self.version_poll_timer.stop()
        self.state_poll_timer.stop()
        self.error_poll_timer.stop()
        self.control_mode_poll_timer.stop()
        self.version_reply_timeout_timer.stop()
        self._connected = False
        self._current_state = None
        self._current_control_mode = None
        self.driver_status_label.setToolTip("")
        self._set_driver_status("ERROR", "error")
        self.state_label.setText("Disconnected")
        self.control_mode_label.setText("Disconnected")
        self._show_error_value(self.active_errors_label, "Disconnected")
        self._show_error_value(self.latched_errors_label, "Disconnected")
        self._set_command_buttons_enabled(False)

    def _set_command_buttons_enabled(self, enabled: bool):
        for button in (
            self.version_button,
            self.state_button,
            self.stop_state_button,
            self.run_state_button,
            self.state_combo,
            self.go_to_state_button,
            self.read_active_errors_button,
            self.read_latched_errors_button,
        ):
            button.setEnabled(enabled)

        can_flash = enabled and self._current_state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR)
        for button in (
            self.flash_save_button,
            self.flash_load_button,
            self.flash_clear_button,
        ):
            button.setEnabled(can_flash)
        self.enter_bootloader_button.setEnabled(can_flash)
        self.clear_latched_errors_button.setEnabled(enabled)
        can_select_mode = enabled and self._current_state == FOCState.FOC_STATE_RUN
        self.control_mode_combo.setEnabled(can_select_mode)
        self.set_control_mode_button.setEnabled(can_select_mode)
        self._refresh_encoder_controls()

    def request_errors(self):
        if self._connected:
            self.request_active_errors()
            self.request_latched_errors()

    def request_active_errors(self):
        self._send(MsgType.MSG_GET_ACTIVE_ERRORS)

    def request_latched_errors(self):
        self._send(MsgType.MSG_GET_LATCHED_ERRORS)

    def clear_latched_errors(self):
        if not self._connected:
            return
        self._send(MsgType.MSG_CLEAR_LATCHED_ERRORS)
        self._show_error_value(self.latched_errors_label, "Clear requested")
        self.request_active_errors()
        self.request_latched_errors()
        self.request_state()

    @staticmethod
    def _style_error_label(label: QLabel, value: int | None):
        if value is None:
            color = "#666666"
        else:
            color = "#16803c" if value == 0 else "#c62828"
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _show_error_value(self, label: QLabel, text: str):
        label.setText(text)
        self._style_error_label(label, None)

    def set_active_errors(self, value: int):
        self.active_errors_label.setText(self._format_error_flags(value))
        self._style_error_label(self.active_errors_label, value)

    def set_latched_errors(self, value: int):
        self.latched_errors_label.setText(self._format_error_flags(value))
        self._style_error_label(self.latched_errors_label, value)

    @staticmethod
    def _format_error_flags(value: int) -> str:
        value &= 0xFFFFFFFF
        known_mask = 0
        active_reasons = []
        for error, label in FOC_ERROR_LABELS.items():
            bit = int(error)
            known_mask |= bit
            if value & bit:
                active_reasons.append(label)

        unknown_bits = value & ~known_mask
        if unknown_bits:
            active_reasons.append(f"Unknown flags 0x{unknown_bits:08X}")

        reasons = ", ".join(active_reasons) if active_reasons else "none"
        return f"0x{value:08X} ({reasons})"

    def request_control_mode(self):
        if self._connected and self._current_state == FOCState.FOC_STATE_RUN:
            self._send(MsgType.MSG_GET_CONTROL_MODE)

    def set_selected_control_mode(self):
        if not self._connected or self._current_state != FOCState.FOC_STATE_RUN:
            return
        try:
            mode = ControlMode(int(self.control_mode_combo.currentData()))
        except (TypeError, ValueError):
            return
        self.on_command(
            Packet(
                msg_type=MsgType.MSG_SET_CONTROL_MODE,
                data=ControlModePayload(mode=mode),
            )
        )
        self.request_control_mode()
        # Switching modes can reset the mode's setpoint. Refresh the matching
        # target fields so the Setpoints tab immediately reflects the driver.
        mode_variable_names = {
            ControlMode.CONTROL_MODE_OPENLOOP: ("dq_current_setpoint.d", "dq_current_setpoint.q"),
            ControlMode.CONTROL_MODE_POSITION: ("angle_setpoint",),
            ControlMode.CONTROL_MODE_SPEED: ("speed_setpoint",),
        }
        for name in mode_variable_names[mode]:
            if name not in self.variable_ids:
                continue
            var_id = self.variable_ids[name]
            self.on_command(
                Packet(
                    msg_type=MsgType.MSG_GET_VAR,
                    data=VarPayload(var_id=var_id, value=None),
                )
            )

    def set_control_mode(self, mode_value: int | ControlMode):
        if not self._connected or self._current_state != FOCState.FOC_STATE_RUN:
            return
        try:
            mode = ControlMode(int(mode_value))
        except (TypeError, ValueError):
            self._current_control_mode = None
            self.control_mode_label.setText("Unknown control mode")
            return
        self._current_control_mode = mode
        self.control_mode_label.setText(CONTROL_MODE_LABELS[mode])

    def request_version(self):
        if not self._connected or self.version_reply_timeout_timer.isActive():
            return
        self.on_command(Packet(msg_type=MsgType.MSG_GET_DEVICE_INFO, data=None))
        self.version_reply_timeout_timer.start()

    def _on_version_timeout(self):
        if self._connected:
            self._set_driver_status("ERROR", "error")
            self.driver_status_label.setToolTip(
                "No valid DEVICE_INFO_REPLY was received before the timeout."
            )

    def request_state(self):
        self._send(MsgType.MSG_GET_STATE)

    def request_encoder_info(self):
        self._send(MsgType.MSG_GET_ENCODER_INFO)

    def _build_encoder_controls(self, layout: QVBoxLayout):
        group = QGroupBox("Encoder")
        form = QFormLayout(group)
        self.encoder_source_combo = QComboBox()
        self.encoder_source_button = BusyButton("Apply source")
        self.encoder_source_button.setToolTip("Stationary IDLE/ERROR only. A different source requires alignment and calibration again. Save settings to persist selection.")
        self.encoder_source_button.activated.connect(self._apply_encoder_source)
        form.addRow("Angle source", self.encoder_source_combo)
        form.addRow(self.encoder_source_button)
        self.encoder_correction_checkbox = QCheckBox("Use calibration table")
        self.encoder_correction_button = BusyButton("Apply correction setting")
        self.encoder_correction_button.activated.connect(self._apply_encoder_correction)
        form.addRow(self.encoder_correction_checkbox)
        form.addRow(self.encoder_correction_button)
        self.encoder_status_label = QLabel("Waiting for encoder info")
        self.encoder_status_label.setWordWrap(True)
        form.addRow(self.encoder_status_label)
        self.encoder_calibrate_button = BusyButton("Calibrate encoder and save")
        self.encoder_calibrate_button.setToolTip("Rotates the unloaded motor in both directions, validates a correction table and saves settings. STOP cancels measurement.")
        self.encoder_calibrate_button.activated.connect(self._start_encoder_calibration)
        form.addRow(self.encoder_calibrate_button)
        layout.addWidget(group)

    def _clear_encoder_info(self):
        self._encoder_info = None
        self.encoder_source_combo.clear()
        self.encoder_correction_checkbox.setChecked(False)
        self.encoder_status_label.setText("Waiting for encoder info")

    def _refresh_encoder_controls(self):
        info = self._encoder_info
        editable = self._connected and not self._calibration_transfer_busy and info is not None and self._current_state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR)
        self.encoder_source_combo.setEnabled(editable)
        self.encoder_source_button.setEnabled(editable)
        if not self._connected:
            source_reason = "Not connected. Connect to the driver first."
        elif info is None:
            source_reason = "Waiting for encoder source information from the driver."
        elif self._calibration_transfer_busy:
            source_reason = "A calibration table transfer is in progress. Wait for it to finish before changing encoder source."
        elif not editable:
            source_reason = "Encoder source changes require stationary IDLE or ERROR. Stop the motor/calibration first."
        else:
            source_reason = "Select an encoder source and apply it. Changing source invalidates alignment, encoder correction and anti-cogging; save settings separately to persist it."
        self.encoder_source_combo.setToolTip(source_reason)
        self.encoder_source_button.setToolTip(source_reason)
        can_correct = editable and info is not None and bool(info.flags & 1)
        self.encoder_correction_checkbox.setEnabled(can_correct)
        self.encoder_correction_button.setEnabled(can_correct)
        calibratable = info is not None and any(source.source_id == info.selected and source.calibratable for source in info.sources)
        self.encoder_calibrate_button.setEnabled(self._connected and self._current_state == FOCState.FOC_STATE_IDLE and
            info is not None and info.flags & 12 == 12 and calibratable)

    def set_encoder_info(self, info: EncoderInfoPayload):
        if not self._connected:
            return
        previous = self._encoder_info
        if previous is None or previous.sources != info.sources:
            self.encoder_source_combo.clear()
            for source in info.sources:
                self.encoder_source_combo.addItem(source.name, source.source_id)
        if previous is None or previous.sources != info.sources or previous.selected != info.selected:
            self.encoder_source_combo.setCurrentIndex(self.encoder_source_combo.findData(info.selected))
        if previous is None or bool(previous.flags & 2) != bool(info.flags & 2):
            self.encoder_correction_checkbox.setChecked(bool(info.flags & 2))
        self._encoder_info = info
        stage_name = ("Not started", "Locking rotor", "Moving", "Measuring", "Validating",
                      "Saving", "Complete", "Failed", "Cancelled")[info.stage]
        self.encoder_status_label.setText(
            f"{'Healthy' if info.flags & 8 else 'Unavailable'}; "
            f"{'aligned' if info.flags & 4 else 'alignment required'}; "
            f"{'valid table' if info.flags & 1 else 'no valid table'}.\n"
            f"{stage_name}: {info.progress / 10:g}%" +
            (f"; error {info.error}." if info.error else "."))
        self._refresh_encoder_controls()

    def _apply_encoder_source(self):
        selected = self.encoder_source_combo.currentData()
        if not self._connected or selected is None or not self.encoder_source_button.isEnabled():
            return
        self.on_command(Packet(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(int(selected))))
        self._send(MsgType.MSG_GET_ENCODER_INFO)

    def _apply_encoder_correction(self):
        if not self._connected or not self.encoder_correction_button.isEnabled():
            return
        self.on_command(Packet(MsgType.MSG_SET_ENCODER_CORRECTION,
                               EncoderCorrectionPayload(self.encoder_correction_checkbox.isChecked())))
        self._send(MsgType.MSG_GET_ENCODER_INFO)

    def _start_encoder_calibration(self):
        if not self._connected or not self.encoder_calibrate_button.isEnabled():
            return
        answer = QMessageBox.question(self, "Encoder calibration",
            "The motor will rotate in both directions. Remove the load and keep the shaft clear. "
            "A successful calibration saves all current settings and invalidates anti-cogging. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._send_state(FOCState.FOC_STATE_ENCODER_CALIBRATION)
            self.request_state()

    def _send_state(self, state: FOCState):
        if self._connected:
            self.on_command(
                Packet(msg_type=MsgType.MSG_SET_STATE, data=StatePayload(state=int(state)))
            )

    def _send_selected_state(self):
        state_value = self.state_combo.currentData()
        if state_value is not None:
            if FOCState(int(state_value)) == FOCState.FOC_STATE_ENCODER_CALIBRATION:
                self._start_encoder_calibration()
            else:
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
        was_running = self._current_state == FOCState.FOC_STATE_RUN
        try:
            state = FOCState(state_value)
            if state == FOCState.FOC_STATE_COUNT:
                raise ValueError("FOC_STATE_COUNT is not a runtime state")
            self._current_state = state
            state_name = state.name.removeprefix("FOC_STATE_").replace("_", " ")
            self.state_label.setText(state_name)
        except ValueError:
            self._current_state = None
            self.state_label.setText("Unknown state")

        is_running = self._current_state == FOCState.FOC_STATE_RUN
        if is_running:
            if not was_running:
                self._current_control_mode = None
                self.control_mode_label.setText("Reading control mode…")
                self.request_control_mode()
            if (
                self._control_mode_poll_interval_ms > 0
                and not self.control_mode_poll_timer.isActive()
            ):
                self.control_mode_poll_timer.start()
        else:
            self.control_mode_poll_timer.stop()
            self._current_control_mode = None
            self.control_mode_label.setText("Not available outside RUN")

        self._set_command_buttons_enabled(self._connected)
