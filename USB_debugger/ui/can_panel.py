from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from protocol_codec import CanHeartbeatPayload, NodeIdPayload, Packet
from protocol_definitions import MsgType


class CanInterfaceDock(QDockWidget):
    """Reads and edits CAN identity and heartbeat rate."""

    def __init__(self, on_command, parent=None):
        super().__init__("CAN Interface", parent)
        self.on_command = on_command
        self._connected = False

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

        heartbeat_group = QGroupBox("CAN heartbeat")
        heartbeat_form = QFormLayout(heartbeat_group)
        self.heartbeat_label = QLabel("Not read")
        self.heartbeat_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        heartbeat_current = QWidget()
        heartbeat_current_layout = QHBoxLayout(heartbeat_current)
        heartbeat_current_layout.setContentsMargins(0, 0, 0, 0)
        heartbeat_current_layout.addWidget(self.heartbeat_label)
        heartbeat_current_layout.addStretch()
        self.read_heartbeat_button = QPushButton("Read")
        self.read_heartbeat_button.clicked.connect(self.request_heartbeat)
        heartbeat_current_layout.addWidget(self.read_heartbeat_button)
        heartbeat_form.addRow("Current rate", heartbeat_current)

        self.heartbeat_input = QSpinBox()
        self.heartbeat_input.setRange(0, 65535)
        self.heartbeat_input.setSuffix(" ms")
        self.heartbeat_input.setToolTip("0 disables CAN heartbeat messages.")
        self.set_heartbeat_button = QPushButton("Set rate")
        self.set_heartbeat_button.clicked.connect(self.set_heartbeat)
        heartbeat_set = QWidget()
        heartbeat_set_layout = QHBoxLayout(heartbeat_set)
        heartbeat_set_layout.setContentsMargins(0, 0, 0, 0)
        heartbeat_set_layout.addWidget(self.heartbeat_input, 1)
        heartbeat_set_layout.addWidget(self.set_heartbeat_button)
        heartbeat_form.addRow("New rate", heartbeat_set)
        layout.addWidget(heartbeat_group)

        layout.addStretch()
        self.setWidget(panel)
        self._update_controls_enabled()

    def begin_connection(self):
        self._connected = False
        self.node_id_label.setText("Waiting for reply")
        self.heartbeat_label.setText("Waiting for reply")
        self._update_controls_enabled()

    def transport_ready(self):
        self._connected = True
        self._update_controls_enabled()

    def set_disconnected(self):
        self._connected = False
        self.node_id_label.setText("Disconnected")
        self.heartbeat_label.setText("Disconnected")
        self._update_controls_enabled()

    def _update_controls_enabled(self):
        for button in (
            self.read_node_id_button,
            self.set_node_id_button,
            self.read_heartbeat_button,
            self.set_heartbeat_button,
        ):
            button.setEnabled(self._connected)
        self.node_id_input.setEnabled(self._connected)
        self.heartbeat_input.setEnabled(self._connected)

    def _send(self, msg_type: MsgType, data=None):
        if self._connected:
            self.on_command(Packet(msg_type=msg_type, data=data))

    def request_all(self):
        if not self._connected:
            return
        self.request_node_id()
        self.request_heartbeat()

    def request_node_id(self):
        self._send(MsgType.MSG_GET_NODE_ID)

    def set_node_id(self):
        self._send(
            MsgType.MSG_SET_NODE_ID,
            NodeIdPayload(node_id=self.node_id_input.value()),
        )
        self.request_node_id()

    def set_node_id_value(self, node_id: int):
        self.node_id_label.setText(
            "Unassigned (0)" if node_id == 0 else str(node_id)
        )
        self.node_id_input.setValue(node_id)

    def request_heartbeat(self):
        self._send(MsgType.MSG_GET_CAN_HEARTBEAT)

    def set_heartbeat(self):
        self._send(
            MsgType.MSG_SET_CAN_HEARTBEAT,
            CanHeartbeatPayload(rate_ms=self.heartbeat_input.value()),
        )
        self.request_heartbeat()

    def set_heartbeat_value(self, rate_ms: int):
        self.heartbeat_input.setValue(rate_ms)
        self.heartbeat_label.setText(
            "Disabled (0 ms)" if rate_ms == 0 else f"{rate_ms} ms"
        )
