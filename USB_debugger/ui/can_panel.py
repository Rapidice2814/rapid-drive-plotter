from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import CAN_CYCLIC_RATE_OPTIONS, SAMPLE_RATE
from protocol_codec import CanCyclicRatePayload, CanCyclicTypePayload, NodeIdPayload, Packet
from protocol_definitions import MsgType


class CanInterfaceDock(QDockWidget):
    """Reads and edits CAN identity and per-message cyclic rates."""

    def __init__(self, on_command, parent=None):
        super().__init__("CAN Interface", parent)
        self.on_command = on_command
        self._connected = False
        self.cyclic_rate_widgets: dict[int, dict[str, QWidget]] = {}

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        node_group = QGroupBox("CAN node ID")
        node_form = QFormLayout(node_group)
        self.node_id_label = QLabel("Not read")
        self.node_id_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        node_current = QWidget()
        node_current_layout = QHBoxLayout(node_current)
        node_current_layout.setContentsMargins(0, 0, 0, 0)
        node_current_layout.addWidget(self.node_id_label)
        node_current_layout.addStretch()
        self.read_node_id_button = QPushButton("Read")
        self.read_node_id_button.clicked.connect(self.request_node_id)
        node_current_layout.addWidget(self.read_node_id_button)
        node_form.addRow("Current", node_current)

        self.node_id_input = QSpinBox()
        self.node_id_input.setRange(0, 15)
        self.node_id_input.setToolTip("0 is unassigned; valid CAN node IDs are 1-15.")
        self.set_node_id_button = QPushButton("Set node ID")
        self.set_node_id_button.clicked.connect(self.set_node_id)
        node_set = QWidget()
        node_set_layout = QHBoxLayout(node_set)
        node_set_layout.setContentsMargins(0, 0, 0, 0)
        node_set_layout.addWidget(self.node_id_input, 1)
        node_set_layout.addWidget(self.set_node_id_button)
        node_form.addRow("New ID", node_set)
        layout.addWidget(node_group)

        rates_group = QGroupBox("CAN cyclic rates")
        rates_form = QFormLayout(rates_group)
        for cyclic_type, label in CAN_CYCLIC_RATE_OPTIONS:
            cyclic_type = int(cyclic_type)
            current_label = QLabel("Not read")
            current_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            current_row = QWidget()
            current_layout = QHBoxLayout(current_row)
            current_layout.setContentsMargins(0, 0, 0, 0)
            current_layout.addWidget(current_label)
            current_layout.addStretch()
            read_button = QPushButton("Read")
            read_button.clicked.connect(
                lambda _checked=False, kind=cyclic_type: self.request_cyclic_rate(kind)
            )
            current_layout.addWidget(read_button)
            rates_form.addRow(f"{label} - current", current_row)

            rate_input = QDoubleSpinBox()
            rate_input.setDecimals(0)
            rate_input.setRange(0, 0xFFFFFFFF)
            rate_input.setSingleStep(1)
            rate_input.setSuffix(" cycles")
            rate_input.setKeyboardTracking(False)
            rate_input.setToolTip(
                f"Rate is in cycles of the {SAMPLE_RATE:g} Hz sample clock. "
                "0 disables this cyclic message."
            )
            set_button = QPushButton("Set rate")
            set_button.clicked.connect(
                lambda _checked=False, kind=cyclic_type: self.set_cyclic_rate(kind)
            )
            rate_row = QWidget()
            rate_layout = QHBoxLayout(rate_row)
            rate_layout.setContentsMargins(0, 0, 0, 0)
            rate_layout.addWidget(rate_input, 1)
            rate_layout.addWidget(set_button)
            rates_form.addRow(f"{label} - new", rate_row)

            self.cyclic_rate_widgets[cyclic_type] = {
                "label": current_label,
                "input": rate_input,
                "read_button": read_button,
                "set_button": set_button,
            }
        layout.addWidget(rates_group)
        layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(panel)
        self.setWidget(scroll_area)
        self._update_controls_enabled()

    def begin_connection(self):
        self._connected = False
        self.node_id_label.setText("Waiting for reply")
        for widgets in self.cyclic_rate_widgets.values():
            widgets["label"].setText("Waiting for reply")
        self._update_controls_enabled()

    def transport_ready(self):
        self._connected = True
        self._update_controls_enabled()

    def set_disconnected(self):
        self._connected = False
        self.node_id_label.setText("Disconnected")
        for widgets in self.cyclic_rate_widgets.values():
            widgets["label"].setText("Disconnected")
        self._update_controls_enabled()

    def _update_controls_enabled(self):
        self.read_node_id_button.setEnabled(self._connected)
        self.set_node_id_button.setEnabled(self._connected)
        self.node_id_input.setEnabled(self._connected)
        for widgets in self.cyclic_rate_widgets.values():
            widgets["read_button"].setEnabled(self._connected)
            widgets["set_button"].setEnabled(self._connected)
            widgets["input"].setEnabled(self._connected)

    def _send(self, msg_type: MsgType, data=None):
        if self._connected:
            self.on_command(Packet(msg_type=msg_type, data=data))

    def request_all(self):
        if not self._connected:
            return
        self.request_node_id()
        for cyclic_type, _label in CAN_CYCLIC_RATE_OPTIONS:
            self.request_cyclic_rate(int(cyclic_type))

    def request_node_id(self):
        self._send(MsgType.MSG_GET_NODE_ID)

    def set_node_id(self):
        self._send(
            MsgType.MSG_SET_NODE_ID,
            NodeIdPayload(node_id=int(self.node_id_input.value())),
        )
        self.request_node_id()

    def set_node_id_value(self, node_id: int):
        self.node_id_label.setText(
            "Unassigned (0)" if node_id == 0 else str(node_id)
        )
        self.node_id_input.setValue(node_id)

    def request_cyclic_rate(self, cyclic_type: int):
        self._send(
            MsgType.MSG_GET_CAN_CYCLIC_RATE,
            CanCyclicTypePayload(cyclic_type=cyclic_type),
        )

    def set_cyclic_rate(self, cyclic_type: int):
        widgets = self.cyclic_rate_widgets.get(cyclic_type)
        if widgets is None:
            return
        self._send(
            MsgType.MSG_SET_CAN_CYCLIC_RATE,
            CanCyclicRatePayload(
                cyclic_type=cyclic_type,
                rate_cycles=int(widgets["input"].value()),
            ),
        )
        self.request_cyclic_rate(cyclic_type)

    def set_cyclic_rate_value(self, cyclic_type: int, rate_cycles: int):
        widgets = self.cyclic_rate_widgets.get(cyclic_type)
        if widgets is None:
            return
        widgets["input"].setValue(rate_cycles)
        widgets["label"].setText(
            "Disabled (0 cycles)" if rate_cycles == 0 else f"{rate_cycles} cycles"
        )
