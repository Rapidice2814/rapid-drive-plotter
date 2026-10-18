"""Production configuration controls generated exclusively from driver metadata."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDockWidget, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from protocol_codec import Packet
from protocol_definitions import MsgType
from configuration_protocol import encode_configuration, decode_configuration


class ConfigurationDock(QDockWidget):
    finished = Signal(object)

    def __init__(self, send, parent=None):
        super().__init__("Configuration", parent)
        self.send = send
        self.entries = []
        self.connected = False
        self.state = None
        self.generation = 0
        self.pending = set()
        self.dirty = set()
        self.values = {}
        self.finished.connect(self._finished)
        self.set_definitions(None)

    def set_definitions(self, entries):
        """Rebuild controls on discovery, discarding all previous session IDs."""
        self.generation += 1
        self.entries = [dict(entry) for entry in entries or []]
        self.pending.clear()
        self.dirty.clear()
        self.values.clear()
        self.widgets = {}
        self.apply_buttons = {}
        self.read_buttons = {}
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.status = QLabel("Validated settings. Updates apply to RAM atomically per group; save to flash separately. "
                             "Changes can invalidate calibration tables. Bounds are not a guarantee of motor tuning stability."
                             if entries is not None else "This firmware does not advertise validated configuration. Upgrade firmware to USB protocol 8.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for group, name in dict((e["group_id"], e["group"]) for e in self.entries).items():
            box = QGroupBox(name)
            form = QFormLayout(box)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for entry in self.entries:
                if entry["group_id"] != group:
                    continue
                identifier = entry["id"]
                if entry["type"] == "bool":
                    widget = QCheckBox()
                    widget.clicked.connect(lambda _checked, i=identifier: self._edited(i))
                else:
                    widget = QLineEdit()
                    widget.setPlaceholderText("Not read")
                    widget.textEdited.connect(lambda _text, i=identifier: self._edited(i))
                widget.setToolTip(f"{entry['name']}\nRange: {entry['minimum']} to {entry['maximum']} {entry['unit']}")
                self.widgets[identifier] = widget
                form.addRow(f"{entry['label']} ({entry['unit']})", widget)
            read = QPushButton("Read group")
            read.clicked.connect(lambda _checked=False, g=group: self.read(g, confirm=True))
            apply = QPushButton("Apply edited settings to RAM")
            apply.clicked.connect(lambda _checked=False, g=group: self.apply(g))
            self.read_buttons[group] = read
            self.apply_buttons[group] = apply
            form.addRow(read, apply)
            layout.addWidget(box)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        old = self.widget()
        self.setWidget(scroll)
        if old is not None:
            old.deleteLater()
        self._permissions()

    def set_connected(self, connected):
        self.generation += 1
        self.connected = connected
        self.state = None
        self.pending.clear()
        self.dirty.clear()
        self.values.clear()
        for widget in self.widgets.values():
            if isinstance(widget, QLineEdit):
                widget.clear()
            else:
                widget.setChecked(False)
        self._permissions()
        if connected:
            for group in self.read_buttons:
                self.read(group)

    def set_state(self, state):
        self.state = state
        self._permissions()

    def _edited(self, identifier):
        self.dirty.add(identifier)
        self._permissions()

    def _permissions(self):
        for group, button in self.apply_buttons.items():
            entries = [e for e in self.entries if e["group_id"] == group]
            allowed = self.state is not None and all(e["states"] & (1 << int(self.state)) for e in entries)
            reason = ("Not connected." if not self.connected else "Command in progress." if group in self.pending else
                      "This group cannot be edited in the current driver state. Stop the motor and wait for IDLE." if not allowed else
                      "Read the complete group first." if any(e["id"] not in self.values for e in entries) else
                      "No local edits in this group.")
            ready = self.connected and group not in self.pending
            loaded = all(e["id"] in self.values for e in entries)
            edited = any(e["id"] in self.dirty for e in entries)
            button.setEnabled(ready and allowed and loaded and edited)
            button.setToolTip("Validate and apply the edited fields together. Rejection changes nothing." if button.isEnabled() else reason)
            button.setText("Working…" if group in self.pending else "Apply edited settings to RAM")
            self.read_buttons[group].setEnabled(ready)
            self.read_buttons[group].setToolTip("Read driver RAM values; discarding local edits requires confirmation." if ready else reason)
            for entry in entries:
                self.widgets[entry["id"]].setEnabled(ready and allowed and loaded)
                if not (ready and allowed and loaded):
                    self.widgets[entry["id"]].setToolTip(reason)
                else:
                    self.widgets[entry["id"]].setToolTip(f"{entry['name']}\nRange: {entry['minimum']} to {entry['maximum']} {entry['unit']}")

    def _send(self, packet, group, writing=False):
        generation = self.generation
        self.pending.add(group)
        self._permissions()
        try:
            self.send(packet, completed=lambda success: self.finished.emit((generation, group, writing, success)))
        except (ValueError, TypeError, RuntimeError) as exc:
            self.status.setText(str(exc))
            self.finished.emit((generation, group, writing, False))

    def _finished(self, result):
        generation, group, writing, success = result
        if generation != self.generation or not self.connected:
            return
        self.pending.discard(group)
        if writing and success:
            self.dirty.difference_update(e["id"] for e in self.entries if e["group_id"] == group)
            self.status.setText("Settings applied to RAM. Dependent calibration may be invalidated. Save to flash separately.")
            self.read(group)
        elif not success:
            self.status.setText("Command failed. See terminal for the driver's explanation. Local edits were retained.")
        self._permissions()

    def read(self, group, confirm=False):
        if not self.connected or group in self.pending:
            return
        identifiers = {e["id"] for e in self.entries if e["group_id"] == group}
        if confirm and identifiers & self.dirty:
            if QMessageBox.question(self, "Discard local edits?", "Replace this group's edits with driver values?") != QMessageBox.StandardButton.Yes:
                return
            self.dirty.difference_update(identifiers)
        self._send(Packet(MsgType.MSG_GET_CONFIGURATION, bytes((group,))), group)

    def apply(self, group):
        if not self.apply_buttons[group].isEnabled():
            return
        try:
            values = {}
            for entry in self.entries:
                identifier = entry["id"]
                if entry["group_id"] != group or identifier not in self.dirty:
                    continue
                widget = self.widgets[identifier]
                value = int(widget.isChecked()) if isinstance(widget, QCheckBox) else float(widget.text()) if entry["type"] == "f" else int(widget.text())
                values[identifier] = value
            payload = encode_configuration(self.entries, group, values)
        except (ValueError, OverflowError) as exc:
            self.status.setText(f"Invalid edit: {exc}")
            return
        self._send(Packet(MsgType.MSG_SET_CONFIGURATION, payload), group, writing=True)

    def on_reply(self, payload):
        if not self.connected or not self.entries:
            return
        group, values = decode_configuration(self.entries, payload)
        self.values.update(values)
        for identifier, value in values.items():
            if identifier in self.dirty:
                continue
            widget = self.widgets[identifier]
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(f"{value:.7g}" if isinstance(value, float) else str(value))
        self._permissions()
