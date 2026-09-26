from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QLabel, QCheckBox, QScrollArea, QPushButton
from PySide6.QtCore import Qt

from protocol_codec import Packet
from protocol_definitions import SIGNAL_MASK_BYTES, FOC_USB_DEBUG_SIGNAL_LIST, MsgType


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
        self.current_mask = bytearray(SIGNAL_MASK_BYTES)  # All signals initially disabled
        self._updating_mask = False

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.mask_label = QLabel("Mask: 0x00000000")
        self.mask_label.setWordWrap(True)
        layout.addWidget(self.mask_label)
        self.read_mask_button = QPushButton("Read mask from driver")
        self.read_mask_button.clicked.connect(self.request_mask)
        layout.addWidget(self.read_mask_button)

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
        if not self._updating_mask:
            self.update_mask(send=True)

    def request_mask(self):
        self.on_command(Packet(msg_type=MsgType.MSG_GET_MASK, data=None))

    def set_mask(self, mask: bytes | bytearray):
        if len(mask) != SIGNAL_MASK_BYTES:
            return
        self._updating_mask = True
        try:
            for bit, checkbox in self.signal_checkboxes.items():
                byte_index, bit_index = divmod(bit, 8)
                checkbox.setChecked(bool(mask[byte_index] & (1 << bit_index)))
        finally:
            self._updating_mask = False
        self.update_mask(send=False)

    def _build_mask(self) -> bytearray:
        mask = bytearray(SIGNAL_MASK_BYTES)
        for sig in self.signal_meta:
            bit = int(sig["bit"])
            cb = self.signal_checkboxes[bit]
            if cb.isChecked():
                byte_index = bit // 8
                bit_index = bit & 7
                mask[byte_index] |= (1 << bit_index)
        return mask

    def update_mask(self, send: bool):
        mask = self._build_mask()
        self.current_mask = mask

        # Display the full mask width for readability
        mask_int = int.from_bytes(mask, "little")
        self.mask_label.setText(f"Mask: 0x{mask_int:0{SIGNAL_MASK_BYTES * 2}X}")

        if send:
            self.on_command(Packet(msg_type=MsgType.MSG_STOP_LOG, data=None))
            # Send the full mask in little-endian byte order
            self.on_command(Packet(msg_type=MsgType.MSG_SET_MASK, data=bytes(mask)))
            self.on_command(Packet(msg_type=MsgType.MSG_START_LOG, data=None))