from PySide6.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QDoubleSpinBox,
    QPushButton,
    QLabel,
)
from PySide6.QtCore import Qt

from protocol_codec import VarPayload, Packet
from protocol_definitions import VAR_ID_LIST, MsgType


class VarDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("Variables", parent)
        self.on_command = on_command

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

        for var in VAR_ID_LIST:
            var_id = int(var["id"])
            var_name = var["name"]

            group = QGroupBox(f"{var_name} (id={var_id})")
            form = QFormLayout(group)

            value = QDoubleSpinBox()
            value.setDecimals(6)
            value.setRange(-1e9, 1e9)
            value.setSingleStep(0.001)

            send_btn = QPushButton("Send")

            form.addRow("Value", value)
            form.addRow("", send_btn)

            self.var_widgets[var_id] = {
                "value": value,
                "send_btn": send_btn,
                "name": var_name,
            }

            send_btn.clicked.connect(lambda _checked=False, vid=var_id: self._send_var_update(vid))

            layout.addWidget(group)

        layout.addStretch()
        self.setWidget(panel)

    def _request_all_var_values(self):
        for var in VAR_ID_LIST:
            var_id = int(var["id"])
            self.on_command(Packet(
                msg_type=MsgType.MSG_GET_VAR,
                data=VarPayload(var_id=var_id, value=None),
            ))

    def _send_var_update(self, var_id: int):
        widgets = self.var_widgets.get(var_id)
        if widgets is None:
            return

        self.on_command(Packet(
            msg_type=MsgType.MSG_SET_VAR,
            data=VarPayload(
                var_id=var_id,
                value=float(widgets["value"].value()),
            ),
        ))

    def set_var_value(self, var_id: int, value: float):
        widgets = self.var_widgets.get(var_id)
        if widgets is None:
            return

        widgets["value"].blockSignals(True)
        widgets["value"].setValue(value)
        widgets["value"].blockSignals(False)