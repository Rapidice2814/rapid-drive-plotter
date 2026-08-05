from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QGroupBox, QFormLayout, QDoubleSpinBox, QPushButton
from PySide6.QtCore import Qt

from protocol_codec import PIDPayload, Packet
from protocol_definitions import FOC_PID_CONTROLLERS_LIST, MsgType

class PidDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("PID Controllers", parent)
        self.on_command = on_command

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.pid_widgets = {}

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.pid_refresh_btn = QPushButton("Read PID Values")
        self.pid_refresh_btn.clicked.connect(self._request_all_pid_values)
        layout.addWidget(self.pid_refresh_btn)

        self.pid_send_btn = QPushButton("Send Changes")
        self.pid_send_btn.clicked.connect(self._send_all_pid_updates)
        layout.addWidget(self.pid_send_btn)

        for ctrl in FOC_PID_CONTROLLERS_LIST:
            ctrl_id = int(ctrl["id"])
            ctrl_name = ctrl["name"]

            group = QGroupBox(f"{ctrl_name} (id={ctrl_id})")
            form = QFormLayout(group)

            kp = QDoubleSpinBox()
            ki = QDoubleSpinBox()
            kd = QDoubleSpinBox()

            for w in (kp, ki, kd):
                w.setDecimals(6)
                w.setRange(-1e9, 1e9)
                w.setSingleStep(0.001)

            form.addRow("Kp", kp)
            form.addRow("Ki", ki)
            form.addRow("Kd", kd)

            self.pid_widgets[ctrl_id] = {"kp": kp, "ki": ki, "kd": kd}
            layout.addWidget(group)

        layout.addStretch()
        self.setWidget(panel)

    def _request_all_pid_values(self):
        for ctrl in FOC_PID_CONTROLLERS_LIST:
            ctrl_id = int(ctrl["id"])
            self.on_command(Packet(
                msg_type=MsgType.MSG_GET_PID,
                data=PIDPayload(controller_id=ctrl_id, kp=None, ki=None, kd=None),
            ))

    def _send_all_pid_updates(self):
        for ctrl in FOC_PID_CONTROLLERS_LIST:
            ctrl_id = int(ctrl["id"])
            widgets = self.pid_widgets.get(ctrl_id)
            if widgets is None:
                continue

            self.on_command(Packet(
                msg_type=MsgType.MSG_SET_PID,
                data=PIDPayload(
                    controller_id=ctrl_id,
                    kp=float(widgets["kp"].value()),
                    ki=float(widgets["ki"].value()),
                    kd=float(widgets["kd"].value()),
                ),
            ))

    def set_pid_values(self, controller_id: int, kp: float, ki: float, kd: float):
        widgets = self.pid_widgets.get(controller_id)
        if widgets is None:
            return

        widgets["kp"].blockSignals(True)
        widgets["ki"].blockSignals(True)
        widgets["kd"].blockSignals(True)

        widgets["kp"].setValue(kp)
        widgets["ki"].setValue(ki)
        widgets["kd"].setValue(kd)

        widgets["kp"].blockSignals(False)
        widgets["ki"].blockSignals(False)
        widgets["kd"].blockSignals(False)