from ui.busy_button import BusyButton
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QLabel, QCheckBox, QScrollArea
from PySide6.QtCore import Qt

from protocol_codec import Packet
from protocol_definitions import MsgType


class SignalSelectorDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("Signals", parent)
        self.on_command = on_command
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.signal_meta = []
        self.signal_checkboxes = {}
        self.current_mask = bytearray()
        self.mask_bytes = self.max_signals = 0
        self._updating_mask = False
        self._connected = False
        self.saved_names = None
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.mask_label = QLabel("Waiting for driver definitions")
        layout.addWidget(self.mask_label)
        self.read_mask_button = BusyButton("Read mask from driver")
        self.read_mask_button.activated.connect(self.request_mask)
        self.read_mask_button.setEnabled(False)
        layout.addWidget(self.read_mask_button)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        scroll.setWidget(self.list_container)
        layout.addWidget(scroll)
        self.setWidget(panel)

    def selected_names(self):
        if not self.signal_meta:
            return self.saved_names
        return [entry["name"] for entry in self.signal_meta
                if self.signal_checkboxes[entry["bit"]].isChecked()]

    def clear_definitions(self):
        self.saved_names = self.selected_names()
        self._connected = False
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.signal_checkboxes.clear()
        self.signal_meta = []
        self.current_mask = bytearray()
        self.mask_bytes = self.max_signals = 0
        self.read_mask_button.setEnabled(False)
        self.mask_label.setText("Waiting for driver definitions")

    def set_definitions(self, definitions, info, mask):
        self.clear_definitions()
        self.signal_meta = [dict(entry) for entry in definitions.signals]
        self.mask_bytes, self.max_signals = info.mask_bytes, info.max_signals
        for entry in self.signal_meta:
            bit = entry["bit"]
            checkbox = QCheckBox(entry["name"])
            checkbox.stateChanged.connect(self._on_signal_toggled)
            self.signal_checkboxes[bit] = checkbox
            self.list_layout.addWidget(checkbox)
        self.list_layout.addStretch()
        self.set_mask(mask)

    def set_connected(self, connected):
        self._connected = connected
        self.read_mask_button.setEnabled(connected)
        self._update_limits()

    def _on_signal_toggled(self):
        if not self._updating_mask:
            self.update_mask(send=self._connected)

    def request_mask(self):
        if self._connected:
            self.on_command(Packet(MsgType.MSG_GET_MASK, None))

    def set_mask(self, mask):
        if len(mask) != self.mask_bytes:
            return
        self._updating_mask = True
        try:
            for bit, checkbox in self.signal_checkboxes.items():
                checkbox.setChecked(bool(mask[bit // 8] & (1 << (bit % 8))))
        finally:
            self._updating_mask = False
        self.update_mask(send=False)

    def _build_mask(self):
        mask = bytearray(self.mask_bytes)
        for bit, checkbox in self.signal_checkboxes.items():
            if checkbox.isChecked():
                mask[bit // 8] |= 1 << (bit % 8)
        return mask

    def _update_limits(self):
        selected = sum(checkbox.isChecked() for checkbox in self.signal_checkboxes.values())
        for checkbox in self.signal_checkboxes.values():
            checkbox.setEnabled(self._connected and (checkbox.isChecked() or selected < self.max_signals))

    def update_mask(self, send):
        self.current_mask = self._build_mask()
        self.saved_names = self.selected_names()
        self._update_limits()
        self.mask_label.setText(f"Mask: 0x{int.from_bytes(self.current_mask, 'little'):0{self.mask_bytes * 2}X}")
        if send:
            self.on_command(Packet(MsgType.MSG_STOP_LOG, None))
            self.on_command(Packet(MsgType.MSG_SET_MASK, bytes(self.current_mask)))
            self.on_command(Packet(MsgType.MSG_START_LOG, None))
