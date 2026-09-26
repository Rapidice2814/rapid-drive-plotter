from PySide6.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QDoubleSpinBox,
    QSpinBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator

from protocol_codec import VarPayload, Packet
from protocol_definitions import FOCState, VAR_ID_LIST, MsgType


class UInt32Validator(QValidator):
    """Accept decimal values in the full unsigned 32-bit range."""

    def validate(self, text: str, position: int):
        if not text:
            return QValidator.State.Intermediate, text, position
        if not text.isdecimal():
            return QValidator.State.Invalid, text, position
        significant_digits = text.lstrip("0") or "0"
        if len(significant_digits) > 10 or int(significant_digits) > 0xFFFFFFFF:
            return QValidator.State.Invalid, text, position
        return QValidator.State.Acceptable, text, position


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class VarDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("Variables", parent)
        self.on_command = on_command
        self._connected = False
        self._driver_state: FOCState | None = None

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.var_widgets = {}

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.read_all_btn = QPushButton("Read Variables")
        self.read_all_btn.clicked.connect(self._request_all_var_values)
        layout.addWidget(self.read_all_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        var_container = QWidget()
        var_layout = QVBoxLayout(var_container)

        for var in VAR_ID_LIST:
            var_id = int(var["id"])
            var_name = var["name"]
            var_type = var["type"]

            group = QGroupBox(f"{var_name} (id={var_id})")
            form = QFormLayout(group)

            if var_type == "f":
                value = NoWheelDoubleSpinBox()
                value.setDecimals(6)
                value.setRange(-1e9, 1e9)
                value.setSingleStep(0.1)
            elif var_type == "i32":
                value = NoWheelSpinBox()
                value.setRange(-2147483648, 2147483647)
                value.setSingleStep(1)
            elif var_type == "u32":
                value = QLineEdit()
                value.setValidator(UInt32Validator(value))
            else:
                raise ValueError(f"Unsupported var type: {var_type}")

            send_btn = QPushButton("Send")

            form.addRow("Value", value)
            form.addRow("", send_btn)

            self.var_widgets[var_id] = {
                "value": value,
                "send_btn": send_btn,
                "name": var_name,
                "type": var_type,
            }

            send_btn.clicked.connect(
                lambda _checked=False, vid=var_id: self._send_var_update(vid)
            )

            var_layout.addWidget(group)

        var_layout.addStretch()
        scroll.setWidget(var_container)
        layout.addWidget(scroll)

        self.setWidget(panel)
        self._update_setpoint_controls_enabled()

    def begin_connection(self):
        self._connected = False
        self._driver_state = None
        self._update_setpoint_controls_enabled()

    def transport_ready(self):
        self._connected = True
        self._update_setpoint_controls_enabled()

    def set_disconnected(self):
        self._connected = False
        self._driver_state = None
        self._update_setpoint_controls_enabled()

    def set_driver_state(self, state_value: int | FOCState | None):
        try:
            state = None if state_value is None else FOCState(int(state_value))
            if state == FOCState.FOC_STATE_COUNT:
                state = None
        except ValueError:
            state = None
        self._driver_state = state if self._connected else None
        self._update_setpoint_controls_enabled()

    def _update_setpoint_controls_enabled(self):
        setpoints_enabled = (
            self._connected and self._driver_state == FOCState.FOC_STATE_RUN
        )
        for var_id in (0, 1, 2, 3):
            widgets = self.var_widgets.get(var_id)
            if widgets is not None:
                widgets["value"].setEnabled(setpoints_enabled)
                widgets["send_btn"].setEnabled(setpoints_enabled)

    def _request_all_var_values(self):
        for var in VAR_ID_LIST:
            var_id = int(var["id"])
            self.on_command(
                Packet(
                    msg_type=MsgType.MSG_GET_VAR,
                    data=VarPayload(var_id=var_id, value=None),
                )
            )

    def _send_var_update(self, var_id: int):
        if var_id in (0, 1, 2, 3) and not (
            self._connected and self._driver_state == FOCState.FOC_STATE_RUN
        ):
            return
        widgets = self.var_widgets.get(var_id)
        if widgets is None:
            return

        value_widget = widgets["value"]
        var_type = widgets["type"]

        if var_type == "f":
            value = float(value_widget.value())
        elif var_type == "i32":
            value = int(value_widget.value())
        elif var_type == "u32":
            text = value_widget.text().strip()
            if not text:
                return
            value = int(text)
            if value < 0 or value > 4294967295:
                return
        else:
            return

        self.on_command(
            Packet(
                msg_type=MsgType.MSG_SET_VAR,
                data=VarPayload(
                    var_id=var_id,
                    value=value,
                ),
            )
        )

    def set_var_value(self, var_id: int, value):
        widgets = self.var_widgets.get(var_id)
        if widgets is None:
            return

        value_widget = widgets["value"]
        var_type = widgets["type"]

        value_widget.blockSignals(True)
        if var_type == "u32":
            value_widget.setText(str(int(value)))
        else:
            value_widget.setValue(value)
        value_widget.blockSignals(False)