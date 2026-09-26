from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from protocol_codec import ControlModePayload, Packet, VarPayload
from protocol_definitions import ControlMode, FOCState, MsgType


SETPOINT_SECTIONS = (
    {
        "key": "open_loop",
        "title": "Open loop",
        "button": "Open loop control",
        "mode": ControlMode.CONTROL_MODE_OPENLOOP,
        "fields": (
            (0, "D-axis current", " A", 0.1),
            (1, "Q-axis current", " A", 0.1),
        ),
    },
    {
        "key": "position",
        "title": "Position control",
        "button": "Position control",
        "mode": ControlMode.CONTROL_MODE_POSITION,
        "fields": ((2, "Angle", " rad", 0.1),),
    },
    {
        "key": "speed",
        "title": "Speed control",
        "button": "Speed control",
        "mode": ControlMode.CONTROL_MODE_SPEED,
        "fields": ((3, "Speed", " rad/s", 1.0),),
    },
)


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class SetpointDock(QDockWidget):
    """Editable setpoints with mutually exclusive motor control modes."""

    def __init__(self, on_command, parent=None):
        super().__init__("Setpoints", parent)
        self.on_command = on_command
        self._connected = False
        self._active_mode: str | None = None
        self._driver_state: FOCState | None = None
        self.value_widgets: dict[int, NoWheelDoubleSpinBox] = {}
        self.set_buttons: dict[int, QPushButton] = {}
        self.mode_buttons: dict[str, QPushButton] = {}
        self.var_modes: dict[int, str] = {}

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        driver_state_group = QGroupBox("Driver state")
        driver_state_layout = QHBoxLayout(driver_state_group)
        driver_state_layout.addWidget(QLabel("Current state"))
        self.driver_state_label = QLabel("Not connected")
        self.driver_state_label.setStyleSheet("color: #1976d2; font-weight: bold;")
        self.driver_state_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        driver_state_layout.addWidget(self.driver_state_label)
        driver_state_layout.addStretch()
        layout.addWidget(driver_state_group)

        layout.addWidget(
            QLabel(
                "Values are read on connect and use the listed units. "
                "Activate a control mode before sending its setpoint."
            )
        )

        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)

        for section in SETPOINT_SECTIONS:
            group = QGroupBox(section["title"])
            group_layout = QVBoxLayout(group)

            mode_button = QPushButton(section["button"])
            mode_button.setCheckable(True)
            mode_button.clicked.connect(
                lambda _checked=False, mode=section["key"]: self._activate_mode(mode)
            )
            self.mode_button_group.addButton(mode_button)
            self.mode_buttons[section["key"]] = mode_button
            group_layout.addWidget(mode_button)

            form = QFormLayout()
            for var_id, label, suffix, step in section["fields"]:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)

                value = NoWheelDoubleSpinBox()
                value.setDecimals(4)
                value.setRange(-1_000_000_000.0, 1_000_000_000.0)
                value.setSingleStep(step)
                value.setSuffix(suffix)
                value.setKeyboardTracking(False)
                value.setToolTip("Enter a target value, then click Set.")

                set_button = QPushButton("Set")
                set_button.clicked.connect(
                    lambda _checked=False, current_id=var_id: self._send_setpoint(
                        current_id
                    )
                )
                row_layout.addWidget(value, 1)
                row_layout.addWidget(set_button)
                form.addRow(label, row)

                self.value_widgets[var_id] = value
                self.set_buttons[var_id] = set_button
                self.var_modes[var_id] = section["key"]

            group_layout.addLayout(form)
            layout.addWidget(group)

        layout.addStretch()
        self.setWidget(panel)
        self._update_controls_enabled()

    def _update_controls_enabled(self):
        in_run_state = self._driver_state == FOCState.FOC_STATE_RUN
        for button in self.mode_buttons.values():
            button.setEnabled(self._connected and in_run_state)
        for var_id, value in self.value_widgets.items():
            active = (
                self._connected
                and in_run_state
                and self._active_mode == self.var_modes[var_id]
            )
            value.setEnabled(active)
            self.set_buttons[var_id].setEnabled(active)

    def _clear_active_mode(self):
        self._active_mode = None
        self.mode_button_group.setExclusive(False)
        for button in self.mode_buttons.values():
            button.setChecked(False)
        self.mode_button_group.setExclusive(True)
        self._update_controls_enabled()

    def begin_connection(self):
        self._connected = False
        self._clear_active_mode()
        self.set_driver_state(None)
        self.driver_state_label.setText("Waiting for driver…")

    def transport_ready(self):
        self._connected = True
        self._update_controls_enabled()
        self.request_values()

    def set_disconnected(self):
        self._connected = False
        self._clear_active_mode()
        self.set_driver_state(None)
        self.driver_state_label.setText("Disconnected")

    def set_driver_state(self, state_value: int | FOCState | None):
        previous_state = self._driver_state
        try:
            current_state = None if state_value is None else FOCState(int(state_value))
            if current_state == FOCState.FOC_STATE_COUNT:
                current_state = None
        except ValueError:
            current_state = None

        if (
            previous_state == FOCState.FOC_STATE_RUN
            and current_state != FOCState.FOC_STATE_RUN
        ):
            # Leaving RUN means the driver has deactivated its control mode and
            # reset its setpoints. Mirror that transition in the UI.
            self._clear_active_mode()
            for widget in self.value_widgets.values():
                widget.blockSignals(True)
                widget.setValue(0.0)
                widget.blockSignals(False)

        self._driver_state = current_state
        if current_state is None:
            self.driver_state_label.setText("Unknown state")
        else:
            state_name = current_state.name.removeprefix("FOC_STATE_").replace("_", " ")
            self.driver_state_label.setText(state_name)
        self._update_controls_enabled()

    def _activate_mode(self, mode: str):
        if (
            not self._connected
            or self._driver_state != FOCState.FOC_STATE_RUN
        ):
            return
        section = next(item for item in SETPOINT_SECTIONS if item["key"] == mode)
        self._active_mode = mode
        self._update_controls_enabled()
        self.on_command(
            Packet(
                msg_type=MsgType.MSG_SET_CONTROL_MODE,
                data=ControlModePayload(mode=section["mode"]),
            )
        )
        self.on_command(Packet(msg_type=MsgType.MSG_GET_CONTROL_MODE, data=None))
        # The driver's mode transition may reset its setpoint values. Read the
        # active target(s) after queuing the mode command instead of assuming 0.
        for var_id, _label, _suffix, _step in section["fields"]:
            self.on_command(
                Packet(
                    msg_type=MsgType.MSG_GET_VAR,
                    data=VarPayload(var_id=var_id, value=None),
                )
            )

    def set_control_mode(self, mode_value: int | ControlMode):
        if not self._connected or self._driver_state != FOCState.FOC_STATE_RUN:
            return
        try:
            mode = ControlMode(int(mode_value))
        except (TypeError, ValueError):
            self._clear_active_mode()
            return

        section = next(
            (item for item in SETPOINT_SECTIONS if item["mode"] == mode), None
        )
        if section is None:
            self._clear_active_mode()
            return

        mode_changed = self._active_mode != section["key"]
        self._active_mode = section["key"]
        for key, button in self.mode_buttons.items():
            button.setChecked(key == self._active_mode)
        self._update_controls_enabled()

        # A mode change may reset the driver's target. Read the newly active
        # setpoint(s) so the editor reflects the actual values, not stale ones.
        if mode_changed:
            for var_id, _label, _suffix, _step in section["fields"]:
                self.on_command(
                    Packet(
                        msg_type=MsgType.MSG_GET_VAR,
                        data=VarPayload(var_id=var_id, value=None),
                    )
                )

    def request_values(self):
        if not self._connected:
            return
        for section in SETPOINT_SECTIONS:
            for var_id, _label, _suffix, _step in section["fields"]:
                self.on_command(
                    Packet(
                        msg_type=MsgType.MSG_GET_VAR,
                        data=VarPayload(var_id=var_id, value=None),
                    )
                )

    def _send_setpoint(self, var_id: int):
        if (
            not self._connected
            or self._driver_state != FOCState.FOC_STATE_RUN
            or self._active_mode != self.var_modes.get(var_id)
        ):
            return
        self.on_command(
            Packet(
                msg_type=MsgType.MSG_SET_VAR,
                data=VarPayload(
                    var_id=var_id,
                    value=float(self.value_widgets[var_id].value()),
                ),
            )
        )

    def set_var_value(self, var_id: int, value: int | float):
        widget = self.value_widgets.get(var_id)
        if widget is None or widget.hasFocus():
            return
        widget.blockSignals(True)
        widget.setValue(float(value))
        widget.blockSignals(False)
