from collections import deque
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QLabel, QCheckBox, QScrollArea
from PySide6.QtCore import Qt

from protocol_codec import Packet
from protocol_definitions import FOC_USB_DEBUG_SIGNAL_LIST, MsgType

class SignalSelectorDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("Signals", parent)
        self.on_command = on_command

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.signal_meta = [s for s in FOC_USB_DEBUG_SIGNAL_LIST if s["name"]]
        self.signal_checkboxes = {}
        self.current_mask = 0

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.mask_label = QLabel("Mask: 0x00000000")
        self.mask_label.setWordWrap(True)
        layout.addWidget(self.mask_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)

        for sig in self.signal_meta:
            bit = int(sig["bit"])
            name = sig["name"]
            cb = QCheckBox(f"bit {bit}: {name}")
            cb.setChecked(False)
            cb.stateChanged.connect(self._on_signal_toggled)
            self.signal_checkboxes[bit] = cb
            list_layout.addWidget(cb)

        list_layout.addStretch()
        scroll.setWidget(list_container)
        layout.addWidget(scroll)

        self.setWidget(panel)

    def _on_signal_toggled(self):
        self.update_mask(send=True)

    def update_mask(self, send: bool):
        mask = 0
        for sig in self.signal_meta:
            bit = int(sig["bit"])
            cb = self.signal_checkboxes[bit]
            if cb.isChecked():
                mask |= (1 << bit)

        self.current_mask = mask
        self.mask_label.setText(f"Mask: 0x{mask:08X}")

        if send:
            self.on_command(Packet(msg_type=MsgType.MSG_STOP_LOG, data=None))
            self.on_command(Packet(msg_type=MsgType.MSG_SET_MASK, data=mask))
            self.on_command(Packet(msg_type=MsgType.MSG_START_LOG, data=None))