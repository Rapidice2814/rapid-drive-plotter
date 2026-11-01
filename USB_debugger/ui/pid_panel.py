"""Shared PID editor and GET_PID/SET_PID workflow for docks and motor presets."""
import struct
from PySide6.QtCore import Qt, Signal
from shiboken6 import isValid
from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QGroupBox,
                               QFormLayout, QDoubleSpinBox, QLabel, QMessageBox,
                               QScrollArea)
from ui.busy_button import BusyButton
from protocol_codec import PIDPayload, Packet
from protocol_definitions import FOCState, MsgType
from pid_protocol import GAINS, pack_gain, validate_partial_gains
from ui.number_format import decimal_text
from ui.setting_label import setting_row, setting_heading, update_setting_label
from ui.setting_permissions import can_apply_setting


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()

    def textFromValue(self, value):
        return decimal_text(value, float32=True).replace(".", self.locale().decimalPoint())


class PidControls(QWidget):
    changed = Signal()
    reply_finished = Signal(object)

    def __init__(self, send, parent=None, show_actions=True):
        super().__init__(parent)
        self.send = send
        self.show_actions = show_actions
        self.controllers = []
        self.pid_widgets = {}
        self.labels, self.reset_buttons = {}, {}
        self.controller_labels, self.controller_reset_buttons = {}, {}
        self.values = {}
        self.dirty = set()
        self.baseline = {}
        self.reference = None
        self.modified = set()
        self.connected = False
        self.state = None
        self.busy = False
        self.external_busy = False
        self.generation = 0
        self.reply_finished.connect(self._reply_finished)
        self.set_definitions([])

    def set_definitions(self, entries):
        self.generation += 1
        self.controllers = [dict(e) for e in entries]
        self.values.clear()
        self.dirty.clear()
        self.baseline.clear()
        self.reference = None
        self.modified.clear()
        self.pid_widgets = {}
        self.labels, self.reset_buttons = {}, {}
        self.controller_labels, self.controller_reset_buttons = {}, {}
        self.busy = False
        self.connected = False
        self.state = None
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
        else:
            while layout.count():
                widget = layout.takeAt(0).widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
        self.pid_refresh_btn = BusyButton("Read PID gains")
        self.pid_refresh_btn.activated.connect(lambda: self.read(confirm=True))
        self.pid_send_btn = BusyButton("Apply edited gains to RAM")
        self.pid_send_btn.activated.connect(lambda: self.apply())
        layout.addWidget(self.pid_refresh_btn)
        layout.addWidget(self.pid_send_btn)
        self.status = QLabel("Read gains from the driver, or load a preset. Only edited gains are applied; save to flash separately.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for widget in (self.pid_refresh_btn, self.pid_send_btn, self.status):
            widget.setVisible(self.show_actions)
        for entry in self.controllers:
            identifier = entry["id"]
            group = QGroupBox()
            group.setAccessibleName(entry["name"])
            group_layout = QVBoxLayout(group)
            heading, label, reset = setting_heading(entry["name"],
                                                    lambda _checked=False, i=identifier: self.reset_controller(i))
            self.controller_labels[identifier] = label
            self.controller_reset_buttons[identifier] = reset
            reset.setToolTip("Reset all gains for this controller")
            group_layout.addWidget(heading)
            form = QFormLayout()
            group_layout.addLayout(form)
            self.pid_widgets[identifier] = {}
            for gain in GAINS:
                field = NoWheelDoubleSpinBox()
                field.setDecimals(45)  # Preserve the full float32 gain range.
                field.setRange(float(entry["minimum"]), float(entry["maximum"]))
                field.setSingleStep(0.1)
                field.setSpecialValueText("Not read")
                field.valueChanged.connect(lambda _value, i=identifier, g=gain: self._edited(i, g))
                field.lineEdit().textEdited.connect(lambda _text, i=identifier, g=gain: self._edited(i, g))
                self.pid_widgets[identifier][gain] = field
                label, row, reset = setting_row(gain.capitalize(), field,
                                                lambda _checked=False, i=identifier, g=gain: self.reset_gain(i, g))
                self.labels[(identifier, gain)] = label
                self.reset_buttons[(identifier, gain)] = reset
                form.addRow(label, row)
            layout.addWidget(group)
        self._permissions()

    def set_connected(self, connected):
        self.generation += 1
        self.connected = connected
        self.state = None
        self.busy = False
        self.values.clear()
        self.dirty.clear()
        self.baseline.clear()
        self.modified.clear()
        self.pid_refresh_btn.reset_busy()
        self.pid_send_btn.reset_busy()
        self._display()
        self._permissions()
        self.changed.emit()

    def set_driver_state(self, state):
        try:
            self.state = FOCState(int(state)) if state is not None else None
            if self.state == FOCState.FOC_STATE_COUNT:
                self.state = None
        except (ValueError, TypeError):
            self.state = None
        self._permissions()

    def set_external_busy(self, busy):
        self.external_busy = busy
        self._permissions()

    def can_write(self, entry):
        return can_apply_setting(self.connected, self.state, entry)

    def has_edits(self):
        return bool(self.dirty)

    def edits_allowed(self):
        return all(self.can_write(e) for e in self.controllers if any(i == e["id"] for i, _g in self.dirty))

    def _permissions(self):
        ready = self.connected and not self.busy and not self.external_busy
        self.pid_refresh_btn.setEnabled(ready and bool(self.controllers))
        self.pid_send_btn.setEnabled(ready and self.has_edits() and self.edits_allowed())
        self.pid_send_btn.setToolTip("Stop the motor and wait for IDLE before applying PID gains."
                                    if not self.edits_allowed() else "")
        local_changes = self.local_changes()
        for entry in self.controllers:
            identifier = entry["id"]
            omitted = self.reference is not None and not any((identifier, g) in self.reference for g in GAINS)
            edited = any(i == identifier for i, _g in self.modified)
            resettable = edited or (self.reference is not None and any(
                (identifier, g) in local_changes and (identifier, g) not in self.reference for g in GAINS))
            self.controller_labels[identifier].setProperty("dirty", edited)
            update_setting_label(self.controller_labels[identifier], self.controller_reset_buttons[identifier], edited, ready,
                                 omitted=omitted, resettable=resettable)
            for gain, field in self.pid_widgets[entry["id"]].items():
                field.setEnabled(ready)
                edited = (entry["id"], gain) in self.modified
                field.setProperty("dirty", edited)
                key = (entry["id"], gain)
                omitted = self.reference is not None and key not in self.reference
                field.setProperty("omitted", omitted)
                update_setting_label(self.labels[key], self.reset_buttons[key], edited, field.isEnabled(), omitted=omitted,
                                     resettable=edited or (omitted and key in local_changes))
                reference = self.reference if self.reference is not None and key in self.reference else self.baseline
                self.reset_buttons[key].setToolTip("Reset to the selected preset value" if self.reference is not None and not omitted else
                                                   "Reset to the last read or loaded value" if key in reference else
                                                   "Clear this edit; no gain was read or loaded")
                field.setToolTip(f"Range: {decimal_text(entry['minimum'])} to {decimal_text(entry['maximum'])}." +
                                 (f"\nPreset value: {decimal_text(self.reference[key], float32=True)}"
                                  if self.reference is not None and not omitted else ""))

    def _edited(self, identifier, gain):
        self.pid_widgets[identifier][gain].setSpecialValueText("")
        key = (identifier, gain)
        entry = next(e for e in self.controllers if e["id"] == identifier)
        value = self.pid_widgets[identifier][gain].value()
        if gain not in self.values.get(identifier, {}) or pack_gain(entry, value) != pack_gain(entry, self.values[identifier][gain]):
            self.dirty.add(key)
        else:
            self.dirty.discard(key)
        self._refresh_modified()
        self._permissions()
        self.changed.emit()

    def reset_gain(self, identifier, gain):
        key = (identifier, gain)
        omitted = self.reference is not None and key not in self.reference
        resettable = key in self.modified or (omitted and key in self.local_changes())
        if self.busy or self.external_busy or not self.connected or not resettable:
            return
        reference = self.reference if self.reference is not None and not omitted else self.baseline
        field = self.pid_widgets[identifier][gain]
        field.blockSignals(True)
        if key in reference:
            field.setSpecialValueText("")
            field.setValue(reference[key])
        else:
            field.setSpecialValueText("Not read")
            field.setValue(field.minimum())
        field.blockSignals(False)
        if key in reference:
            self._edited(identifier, gain)
        else:
            self.dirty.discard(key)
            self._refresh_modified()
            self._permissions()
            self.changed.emit()

    def reset_controller(self, identifier):
        if self.busy or self.external_busy or not self.connected:
            return
        for gain in GAINS:
            self.reset_gain(identifier, gain)

    def _display(self):
        for identifier, fields in self.pid_widgets.items():
            for gain, field in fields.items():
                if (identifier, gain) in self.dirty:
                    continue
                value = self.values.get(identifier, {}).get(gain)
                field.blockSignals(True)
                field.setSpecialValueText("Not read" if value is None else "")
                field.setValue(field.minimum() if value is None else value)
                field.blockSignals(False)

    def stage(self, gains_by_name):
        """Load supplied gains; other gains remain untouched at apply time."""
        for entry in self.controllers:
            validate_partial_gains(entry, gains_by_name.get(entry["name"], {}))
        self.dirty.clear()
        self._display()
        for entry in self.controllers:
            for gain, value in gains_by_name.get(entry["name"], {}).items():
                field = self.pid_widgets[entry["id"]][gain]
                field.blockSignals(True)
                field.setSpecialValueText("")
                field.setValue(value)
                field.blockSignals(False)
                self.dirty.add((entry["id"], gain))
        self.mark_baseline()
        self._permissions()
        self.changed.emit()

    def editor_gains(self):
        return {e["name"]: {g: self.pid_widgets[e["id"]][g].value() for g in GAINS
                            if g in self.values.get(e["id"], {}) or (e["id"], g) in self.dirty}
                for e in self.controllers}

    def mark_baseline(self, gains_by_name=None):
        if gains_by_name is None:
            gains_by_name = self.editor_gains()
            self.baseline.clear()
        self.baseline.update({(e["id"], g): value for e in self.controllers
                              for g, value in gains_by_name.get(e["name"], {}).items()})
        self._refresh_modified()
        self._permissions()

    def replace_values(self, values):
        self.values = {identifier: dict(gains) for identifier, gains in values.items()}
        self.dirty.clear()
        self._display()
        self.mark_baseline()
        self.changed.emit()

    def set_reference(self, gains_by_name):
        self.reference = (None if gains_by_name is None else
                          {(e["id"], g): value for e in self.controllers
                           for g, value in gains_by_name.get(e["name"], {}).items()})
        self._refresh_modified()
        self._permissions()
        self.changed.emit()

    def local_changes(self):
        return {(e["id"], g) for e in self.controllers for g, value in self.editor_gains()[e["name"]].items()
                if (e["id"], g) not in self.baseline or pack_gain(e, value) != pack_gain(e, self.baseline[(e["id"], g)])}

    def _refresh_modified(self):
        self.modified.clear()
        reference = self.reference if self.reference is not None else self.baseline
        for entry in self.controllers:
            for gain, value in self.editor_gains()[entry["name"]].items():
                key = (entry["id"], gain)
                if self.reference is not None and key not in self.reference:
                    continue
                if key not in reference or pack_gain(entry, value) != pack_gain(entry, reference[key]):
                    self.modified.add(key)

    def _request(self, packet, callback):
        generation = self.generation
        replies = []
        def done(success):
            if isValid(self):
                self.reply_finished.emit((generation, packet, callback, success, replies[-1] if replies else None))
        try:
            if packet.msg_type == MsgType.MSG_GET_PID:
                self.send(packet, completed=done, received=replies.append)
            else:
                self.send(packet, completed=done)
        except (ValueError, TypeError, RuntimeError):
            done(False)

    def _reply_finished(self, result):
        generation, packet, callback, success, raw = result
        if generation != self.generation or not self.connected:
            return
        values, error = None, None
        try:
            if not success:
                raise ValueError("Driver rejected or failed the PID command")
            if packet.msg_type == MsgType.MSG_GET_PID:
                if raw is None or raw.msg_type_int != MsgType.MSG_PID_REPLY:
                    raise ValueError("Missing correlated PID readback")
                identifier, *gains = struct.unpack("<Bfff", raw.payload_bytes)
                if identifier != packet.data.controller_id:
                    raise ValueError("PID readback is for a different controller")
                entry = next(e for e in self.controllers if e["id"] == identifier)
                values = dict(zip(GAINS, gains))
                validate_partial_gains(entry, values)
        except (ValueError, TypeError, OverflowError, struct.error) as exc:
            error = str(exc)
        callback(values, error)

    def _get(self, identifier, callback):
        self._request(Packet(MsgType.MSG_GET_PID, PIDPayload(identifier, None, None, None)), callback)

    def _begin(self, button):
        self.busy = True
        token = button.begin_busy()
        self._permissions()
        self.changed.emit()
        return token

    def _end(self, button, token, completed, values, error):
        self.busy = False
        button.end_busy(token)
        self._display()
        self._refresh_modified()
        self.status.setText(f"PID operation stopped: {error}. Earlier successful writes remain in RAM; unapplied edits were retained."
                            if error else "PID gains read from driver RAM." if button is self.pid_refresh_btn else
                            "PID gains applied to RAM and verified. Save to flash separately.")
        self._permissions()
        self.changed.emit()
        if completed is not None:
            completed(error is None, values, error)

    def read(self, completed=None, confirm=False, display=True):
        if not self.connected or self.busy or not self.controllers:
            return
        if confirm and self.dirty:
            if QMessageBox.question(self, "Discard PID edits?", "Replace edited PID gains with current driver values?") != QMessageBox.StandardButton.Yes:
                return
            self.dirty.clear()
        token = self._begin(self.pid_refresh_btn)
        pending = {e["id"] for e in self.controllers}
        snapshot, errors = {}, []
        def received(identifier, values, error):
            pending.discard(identifier)
            if error:
                errors.append(error)
            else:
                snapshot[identifier] = values
                if display:
                    self.values[identifier] = values
                    for gain, value in values.items():
                        if (identifier, gain) not in self.dirty:
                            self.baseline[(identifier, gain)] = value
            if not pending:
                if display and confirm and not errors:
                    self.replace_values(snapshot)
                self._end(self.pid_refresh_btn, token, completed, snapshot, errors[0] if errors else None)
        for entry in self.controllers:
            identifier = entry["id"]
            self._get(identifier, lambda values, error, i=identifier: received(i, values, error))

    def apply(self, completed=None, identifiers=None):
        if not self.connected or self.busy:
            return
        entries = [e for e in self.controllers if (identifiers is None or e["id"] in identifiers)
                   and any(i == e["id"] for i, _gain in self.dirty)]
        if not all(self.can_write(e) for e in entries):
            if completed is not None:
                completed(False, {}, "PID gains are not writable in the current driver state")
            return
        token = self._begin(self.pid_send_btn)
        verified = {}
        def finish(error=None):
            self._end(self.pid_send_btn, token, completed, verified, error)
        def next_controller():
            if not entries:
                finish()
                return
            entry = entries.pop(0)
            identifier = entry["id"]
            edits = {g: self.pid_widgets[identifier][g].value() for g in GAINS if (identifier, g) in self.dirty}
            def readback(values, error):
                if error:
                    finish(error)
                    return
                if any(pack_gain(entry, values[g]) != pack_gain(entry, merged[g]) for g in GAINS):
                    finish("PID readback differs from the requested gains")
                    return
                verified[identifier] = self.values[identifier] = values
                self.dirty.difference_update((identifier, g) for g in edits)
                next_controller()
            def written(_values, error):
                if error:
                    finish(error)
                else:
                    self._get(identifier, readback)
            def merge(values, error):
                nonlocal merged
                if error:
                    finish(error)
                    return
                if not self.can_write(entry):
                    finish("Driver state changed before PID write")
                    return
                merged = dict(values, **edits)
                validate_partial_gains(entry, merged)
                self._request(Packet(MsgType.MSG_SET_PID, PIDPayload(identifier, *(merged[g] for g in GAINS))), written)
            merged = {}
            self._get(identifier, merge)
        next_controller()


class PidDock(QDockWidget):
    def __init__(self, send, parent=None):
        super().__init__("PID Controllers", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.controls = PidControls(send, self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.controls)
        self.setWidget(scroll)

    @property
    def pid_widgets(self): return self.controls.pid_widgets

    @property
    def controllers(self): return self.controls.controllers

    @property
    def pid_refresh_btn(self): return self.controls.pid_refresh_btn

    @property
    def pid_send_btn(self): return self.controls.pid_send_btn

    def set_definitions(self, entries): self.controls.set_definitions(entries)
    def begin_connection(self): self.controls.set_connected(False)
    def transport_ready(self): self.controls.set_connected(True)
    def set_disconnected(self): self.controls.set_connected(False)
    def set_driver_state(self, state): self.controls.set_driver_state(state)
    def _request_all_pid_values(self): self.controls.read()
    def _send_all_pid_updates(self): self.controls.apply()
