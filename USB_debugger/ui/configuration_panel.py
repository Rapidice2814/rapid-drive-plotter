"""Discovered configuration editor with global read/apply and reusable presets."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from shiboken6 import isValid
from PySide6.QtWidgets import (QCheckBox, QDockWidget, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QComboBox, QDialog, QGridLayout, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)
from protocol_codec import Packet
from protocol_definitions import MsgType
from configuration_protocol import encode_configuration, decode_configuration, pack_value
from motor_presets import PresetStore
from pid_protocol import validate_partial_gains
from ui.pid_panel import PidControls
from ui.motor_preset_dialog import MotorPresetDialog
from ui.number_format import decimal_text
from ui.setting_label import setting_row, update_setting_label
from ui.setting_permissions import can_apply_setting

PRESET_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class ConfigurationDock(QDockWidget):
    finished = Signal(object)

    def __init__(self, send, parent=None, preset_store=None):
        super().__init__("Configuration", parent)
        self.send = send
        self.preset_store = preset_store or PresetStore()
        self.connected = False
        self.state = None
        self.generation = 0
        self.pending = set()
        self.values = {}
        self.baseline = {}
        self.reference = None
        self.reference_values = {}
        self.present = set()
        self.staged = set()
        self.dirty = set()
        self.reading = None
        self.applying = False
        self.apply_queue = []
        self.selected_path = None
        self.finished.connect(self._finished)
        self.set_definitions(None)

    def set_definitions(self, entries, pids=None):
        self.generation += 1
        self.pending.clear()
        self.values.clear()
        self.baseline.clear()
        self.present.clear()
        self.staged.clear()
        self.dirty.clear()
        self.reading = None
        self.applying = False
        self.apply_queue = []
        self.entries = [dict(e) for e in entries or []]
        self.pid_entries = [dict(e) for e in pids or []]
        self.groups = list(dict.fromkeys(e["group_id"] for e in self.entries))
        if hasattr(self, "pid_controls"):
            self.pid_controls.deleteLater()
        self.pid_controls = PidControls(self.send, self, show_actions=False)
        self.pid_controls.set_definitions(self.pid_entries)
        self.widgets, self.labels, self.reset_buttons = {}, {}, {}
        panel = QWidget()
        layout = QVBoxLayout(panel)
        presets = QGroupBox("Motor / model preset")
        preset_layout = QVBoxLayout(presets)
        self.preset_combo = QComboBox()
        preset_layout.addWidget(self.preset_combo)
        actions = QGridLayout()
        self.read_driver_button = QPushButton("Read from driver")
        self.apply_driver_button = QPushButton("Apply values to RAM")
        self.save_preset_button = QPushButton("Save preset…")
        self.read_driver_button.clicked.connect(lambda: self.read_from_driver())
        self.apply_driver_button.clicked.connect(self.apply_to_driver)
        self.save_preset_button.clicked.connect(self.save_preset)
        actions.addWidget(self.read_driver_button, 0, 0)
        actions.addWidget(self.apply_driver_button, 0, 1)
        actions.addWidget(self.save_preset_button, 1, 0, 1, 2)
        preset_layout.addLayout(actions)
        folder = QLabel(f"User presets: {self.preset_store.directory}")
        folder.setWordWrap(True)
        preset_layout.addWidget(folder)
        layout.addWidget(presets)
        self.status = QLabel("Read all values from the driver or select a preset. Apply writes to RAM; save to flash separately."
                             if entries is not None else "Connect a driver to discover its configuration fields.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for group, name in dict((e["group_id"], e["group"]) for e in self.entries).items():
            form = QFormLayout(box := QGroupBox(name))
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
                self.widgets[identifier] = widget
                label, row, reset = setting_row(f"{entry['label']} ({entry['unit']})", widget,
                                                lambda _checked=False, i=identifier: self.reset_setting(i))
                self.labels[identifier] = label
                self.reset_buttons[identifier] = reset
                form.addRow(label, row)
            layout.addWidget(box)
        if self.pid_entries:
            box = QGroupBox("PID Controllers")
            QVBoxLayout(box).addWidget(self.pid_controls)
            layout.addWidget(box)
        self.pid_controls.changed.connect(self._permissions)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        previous = self.widget()
        self.setWidget(scroll)
        if previous is not None:
            previous.deleteLater()
        self.refresh_presets()
        self.preset_combo.currentIndexChanged.connect(self.load_preset)
        self._permissions()

    @property
    def busy(self):
        return bool(self.pending or self.reading is not None or self.applying or self.pid_controls.busy)

    def set_connected(self, connected):
        self.generation += 1
        self.connected = connected
        self.state = None
        self.pending.clear()
        self.values.clear()
        self.baseline.clear()
        self.present.clear()
        self.staged.clear()
        self.dirty.clear()
        self.reading = None
        self.applying = False
        self.apply_queue = []
        self.pid_controls.set_connected(connected)
        self._display({})
        self._permissions()
        if connected:
            self.read_from_driver(confirm=False)

    def set_state(self, state):
        self.state = state
        self.pid_controls.set_driver_state(state)
        self._permissions()

    def _allowed(self, entry):
        return can_apply_setting(self.connected, self.state, entry)

    def _field_value(self, entry):
        widget = self.widgets[entry["id"]]
        value = (int(widget.isChecked()) if isinstance(widget, QCheckBox) else
                 float(widget.text()) if entry["type"] == "f" else int(widget.text()))
        pack_value(entry, value)
        return value

    def editor_parameters(self):
        return {e["name"]: self._field_value(e) for e in self.entries if e["id"] in self.present}

    def _configuration_changes(self):
        return {e["id"]: self._field_value(e) for e in self.entries if e["id"] in self.staged and
                (e["id"] not in self.values or pack_value(e, self._field_value(e)) != pack_value(e, self.values[e["id"]]))}

    def has_unapplied_changes(self):
        try:
            return bool(self._configuration_changes() or self.pid_controls.has_edits())
        except (ValueError, OverflowError):
            return True

    def _edited(self, identifier):
        self.present.add(identifier)
        self.staged.add(identifier)
        entry = next(e for e in self.entries if e["id"] == identifier)
        if self._field_modified(entry):
            self.dirty.add(identifier)
        else:
            self.dirty.discard(identifier)
        self._permissions()

    def _field_modified(self, entry):
        if self.reference is not None and entry["id"] not in self.reference_values:
            return False
        reference = self.reference_values if self.reference is not None else self.baseline
        return self._field_differs(entry, reference)

    def _field_differs(self, entry, reference):
        identifier = entry["id"]
        if identifier not in self.present:
            return False
        try:
            return identifier not in reference or pack_value(entry, self._field_value(entry)) != pack_value(entry, reference[identifier])
        except (ValueError, OverflowError):
            return True

    def _has_local_edits(self):
        return any(self._field_differs(e, self.baseline) for e in self.entries) or bool(self.pid_controls.local_changes())

    def _set_reference(self, data):
        self.reference = deepcopy(data) if data is not None else None
        self.reference_values = ({e["id"]: data["parameters"][e["name"]] for e in self.entries
                                  if e["name"] in data["parameters"]} if data is not None else {})
        self.pid_controls.set_reference(data["pids"] if data is not None else None)
        self._permissions()

    def reset_setting(self, identifier):
        entry = next(e for e in self.entries if e["id"] == identifier)
        omitted = self.reference is not None and identifier not in self.reference_values
        resettable = self._field_modified(entry) or (omitted and self._field_differs(entry, self.baseline))
        if self.busy or not self.connected or not resettable:
            return
        reference = self.reference_values if self.reference is not None and not omitted else self.baseline
        widget = self.widgets[identifier]
        if identifier in reference:
            value = reference[identifier]
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(decimal_text(value, float32=entry["type"] == "f"))
            self._edited(identifier)
        else:
            if isinstance(widget, QCheckBox):
                widget.setChecked(False)
            else:
                widget.clear()
            self.present.discard(identifier)
            self.staged.discard(identifier)
            self.dirty.discard(identifier)
            self._permissions()

    def _permissions(self):
        self.dirty = {e["id"] for e in self.entries if self._field_modified(e)}
        self.pid_controls.set_external_busy(bool(self.pending or self.reading is not None or self.applying))
        busy = self.busy
        self.invalid_fields = {}
        for entry in self.entries:
            if entry["id"] not in self.present:
                continue
            try:
                self._field_value(entry)
            except (ValueError, OverflowError) as exc:
                self.invalid_fields[entry["id"]] = str(exc)
        self.read_driver_button.setEnabled(self.connected and bool(self.entries or self.pid_entries) and not busy)
        self.save_preset_button.setEnabled(not busy and not self.invalid_fields)
        self.preset_combo.setEnabled(bool(self.entries or self.pid_entries) and not busy)
        try:
            changes = set(self._configuration_changes())
        except (ValueError, OverflowError):
            changes = set(self.dirty)
        allowed = all(self._allowed(e) for e in self.entries if e["id"] in changes) and self.pid_controls.edits_allowed()
        self.apply_driver_button.setEnabled(self.connected and not busy and not self.invalid_fields and
                                           bool(changes or self.pid_controls.has_edits()) and allowed)
        self.apply_driver_button.setToolTip("Correct invalid values before applying." if self.invalid_fields else
                                           "Stop the motor and wait for IDLE before applying these settings." if not allowed else "")
        for entry in self.entries:
            identifier = entry["id"]
            changed = identifier in self.dirty
            widget = self.widgets[identifier]
            widget.setEnabled(not busy and self.connected)
            widget.setProperty("dirty", changed)
            error = self.invalid_fields.get(identifier)
            widget.setProperty("invalid", error is not None)
            widget.setStyleSheet("QLineEdit { background-color: #ffe8e8; color: #a11919; border: 1px solid #d93025; }"
                                if error is not None else "")
            omitted = self.reference is not None and identifier not in self.reference_values
            widget.setProperty("omitted", omitted)
            update_setting_label(self.labels[identifier], self.reset_buttons[identifier], changed, widget.isEnabled(), omitted=omitted,
                                 resettable=changed or (omitted and self._field_differs(entry, self.baseline)))
            self.reset_buttons[identifier].setToolTip("Reset to the selected preset value" if self.reference is not None and not omitted else
                                                     "Reset to the last read or loaded value" if identifier in self.baseline else
                                                     "Clear this edit; no value was read or loaded")
            widget.setToolTip(f"{entry['name']}\nRange: {decimal_text(entry['minimum'])} to {decimal_text(entry['maximum'])} {entry['unit']}" +
                              (f"\nInvalid value: {error}" if error is not None else
                               f"\nPreset value: {decimal_text(self.reference_values[identifier], float32=entry['type'] == 'f')}"
                               if self.reference is not None and not omitted else ""))

    def _display(self, values):
        for entry in self.entries:
            widget = self.widgets[entry["id"]]
            value = values.get(entry["id"])
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value) if value is not None else False)
            else:
                widget.setText(decimal_text(value, float32=entry["type"] == "f") if value is not None else "")

    def _request(self, packet, key, callback):
        generation = self.generation
        replies = []
        self.pending.add(key)
        self._permissions()
        def done(success):
            if isValid(self):
                self.finished.emit((generation, key, callback, success, replies[-1] if replies else None))
        try:
            if packet.msg_type == MsgType.MSG_GET_CONFIGURATION:
                self.send(packet, completed=done, received=replies.append)
            else:
                self.send(packet, completed=done)
        except (ValueError, TypeError, RuntimeError):
            done(False)

    def _finished(self, result):
        generation, key, callback, success, raw = result
        if generation != self.generation or not self.connected:
            return
        self.pending.discard(key)
        callback(success, raw)
        self._permissions()

    def _decode(self, group, success, raw):
        if not success or raw is None or raw.msg_type_int != MsgType.MSG_CONFIGURATION_REPLY:
            raise ValueError("Driver configuration read failed")
        received, values = decode_configuration(self.entries, raw.payload_bytes)
        if received != group:
            raise ValueError("Readback is for a different configuration group")
        return values

    def read_from_driver(self, confirm=True):
        if not self.connected or self.busy or not (self.entries or self.pid_entries):
            return
        if confirm and self._has_local_edits():
            if QMessageBox.question(self, "Discard local edits?", "Replace all editor values with current driver values?") != QMessageBox.StandardButton.Yes:
                return
        self.reading = {"pending": set(self.groups) | ({"pids"} if self.pid_entries else set()),
                        "values": {}, "pids": {}, "errors": []}
        self.status.setText("Reading all configuration groups and PID gains from driver RAM…")
        generation = self.generation
        def group_read(group, success, raw):
            try:
                self.reading["values"].update(self._decode(group, success, raw))
            except (ValueError, OverflowError) as exc:
                self.reading["errors"].append(str(exc))
            self._read_part_done(group)
        for group in self.groups:
            self._request(Packet(MsgType.MSG_GET_CONFIGURATION, bytes((group,))), group,
                          lambda success, raw, g=group: group_read(g, success, raw))
        if self.pid_entries:
            def pids_read(success, values, error):
                if generation != self.generation or self.reading is None:
                    return
                self.reading["pids"] = values
                if not success:
                    self.reading["errors"].append(error)
                self._read_part_done("pids")
            self.pid_controls.read(completed=pids_read, display=False)
        self._permissions()

    def _read_part_done(self, key):
        self.reading["pending"].discard(key)
        if self.reading["pending"]:
            return
        result, self.reading = self.reading, None
        if result["errors"]:
            self.status.setText("Read failed. Editor values and local edits were retained. " + str(result["errors"][0]))
        else:
            self.values = dict(result["values"])
            self.baseline = dict(self.values)
            self.present = set(self.values)
            self.staged.clear()
            self.dirty.clear()
            self._display(self.values)
            self.pid_controls.replace_values(result["pids"])
            self.status.clear()
        self._permissions()

    def refresh_presets(self, selected=None):
        selected = self.selected_path if selected is None else selected
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Select a preset…", None)
        presets, errors = self.preset_store.list()
        for path, data in presets:
            index = self.preset_combo.count()
            builtin = self.preset_store.is_builtin(path)
            self.preset_combo.addItem(data["name"] + (" (built-in)" if builtin else ""), data)
            self.preset_combo.setItemData(index, str(path.resolve()), PRESET_PATH_ROLE)
            self.preset_combo.setItemData(index, ("Built-in preset (read-only)" if builtin else "User preset") +
                                          f"\n{path}", Qt.ItemDataRole.ToolTipRole)
            if path == selected:
                self.preset_combo.setCurrentIndex(index)
        current = self.preset_combo.currentData(PRESET_PATH_ROLE)
        self.selected_path = Path(current) if current else None
        self.preset_combo.blockSignals(False)
        self._set_reference(self.preset_combo.currentData())
        if errors:
            self.status.setText("Some preset files could not be read: " + "; ".join(errors))

    def _restore_selection(self):
        self.preset_combo.blockSignals(True)
        index = self.preset_combo.findData(str(self.selected_path.resolve()), PRESET_PATH_ROLE) if self.selected_path else 0
        self.preset_combo.setCurrentIndex(max(0, index))
        self.preset_combo.blockSignals(False)

    def load_preset(self, _index=None):
        data = self.preset_combo.currentData()
        if data is None:
            self.selected_path = None
            self._set_reference(None)
            return
        if self.busy:
            return
        if self._has_local_edits() and QMessageBox.question(
                self, "Replace edits?", "Replace local edits with this preset? Omitted settings keep their last read driver values.") != QMessageBox.StandardButton.Yes:
            self._restore_selection()
            return
        by_name = {e["name"]: e for e in self.entries}
        pids_by_name = {e["name"]: e for e in self.pid_entries}
        try:
            for name, value in data["parameters"].items():
                if name in by_name:
                    pack_value(by_name[name], value)
            for name, gains in data["pids"].items():
                if name in pids_by_name:
                    validate_partial_gains(pids_by_name[name], gains)
        except (ValueError, OverflowError) as exc:
            self.status.setText(f"Preset not loaded: {exc}")
            self._restore_selection()
            return
        values = dict(self.values)
        values.update({by_name[name]["id"]: value for name, value in data["parameters"].items() if name in by_name})
        self.present = set(values)
        self.staged = {by_name[name]["id"] for name in data["parameters"] if name in by_name}
        self.baseline = dict(values)
        self.dirty.clear()
        self._display(values)
        self.pid_controls.stage(data["pids"])
        self.selected_path = Path(self.preset_combo.currentData(PRESET_PATH_ROLE))
        self._set_reference(data)
        unknown = (set(data["parameters"]) - set(by_name)) | (set(data["pids"]) - set(pids_by_name))
        self.status.setText("Preset loaded. Edit values, apply to driver RAM, or save the preset." +
                            (" Unsupported fields skipped: " + ", ".join(sorted(unknown)) if unknown else ""))
        self._permissions()

    def save_preset(self):
        if self.busy:
            return
        try:
            parameters = self.editor_parameters()
            pids = self.pid_controls.editor_gains()
            presets, _errors = self.preset_store.list()
            writable = [(path, data) for path, data in presets if self.preset_store.can_update(path)]
            source = next((data for path, data in presets if path == self.selected_path), None)
            builtin = self.selected_path is not None and self.preset_store.is_builtin(self.selected_path)
            new_name = f"{source['name']} (custom)" if builtin and source else ""
            selection = deepcopy(self.reference)
            if selection is not None:
                selection["parameters"].update({e["name"]: parameters[e["name"]] for e in self.entries
                                                if self._field_differs(e, self.baseline)})
                local_gains = self.pid_controls.local_changes()
                for entry in self.pid_entries:
                    for gain, value in pids[entry["name"]].items():
                        if (entry["id"], gain) in local_gains:
                            selection["pids"].setdefault(entry["name"], {})[gain] = value
            dialog = MotorPresetDialog(self.entries, parameters, self, self.pid_entries, pids,
                                       presets=writable, selected_path=self.selected_path, editable=False, new_name=new_name,
                                       selection=selection)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            path = self.preset_store.save(dialog.name.text(), dialog.parameters, dialog.pids, path=dialog.path)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            self.status.setText(f"Could not save preset: {exc}")
            return
        self.baseline.update({e["id"]: dialog.parameters[e["name"]] for e in self.entries if e["name"] in dialog.parameters})
        self.dirty = {e["id"] for e in self.entries if e["id"] in self.present and self._field_modified(e)}
        self.pid_controls.mark_baseline(dialog.pids)
        self.refresh_presets(path)
        self.status.setText(f"Preset saved: {path}. Apply to RAM separately to update the driver.")
        self._permissions()

    def apply_to_driver(self):
        if not self.apply_driver_button.isEnabled():
            return
        try:
            changes = self._configuration_changes()
            grouped = {}
            for entry in self.entries:
                if entry["id"] in changes:
                    grouped.setdefault(entry["group_id"], {})[entry["id"]] = changes[entry["id"]]
            order = list(grouped)
            keys = {e["name"]: e for e in self.entries}
            cal, limit = keys.get("encoder.calibration_current"), keys.get("limits.max_dq_current")
            if cal and limit and cal["group_id"] in grouped and limit["group_id"] in grouped:
                desired = grouped[cal["group_id"]].get(cal["id"], self.values.get(cal["id"], 0))
                if desired <= self.values.get(limit["id"], 0):
                    order.remove(cal["group_id"])
                    order.insert(0, cal["group_id"])
            order.sort(key=lambda g: any(e["group_id"] == g and e["name"] == "controller.current_PID_FF_enabled" for e in self.entries))
            self.apply_queue = [("config", g, grouped[g], encode_configuration(self.entries, g, grouped[g])) for g in order]
            self.apply_queue.extend(("pid", e["id"]) for e in self.pid_entries
                                    if any(i == e["id"] for i, _g in self.pid_controls.dirty))
        except (ValueError, OverflowError) as exc:
            self.status.setText(f"Invalid edit: {exc}")
            return
        self.applying = True
        self._apply_next()

    def _apply_failed(self, error):
        self.applying = False
        self.apply_queue = []
        self.status.setText(f"Apply stopped: {error}. Earlier successful writes remain in RAM; unapplied values were retained.")
        self._permissions()

    def _apply_next(self):
        if not self.apply_queue:
            self.applying = False
            self.status.setText("All requested values applied to driver RAM and verified. Save to flash separately.")
            self._permissions()
            return
        action = self.apply_queue.pop(0)
        if action[0] == "pid":
            generation = self.generation
            def applied(success, _values, error):
                if generation != self.generation or not self.connected:
                    return
                if success:
                    self._apply_next()
                else:
                    self._apply_failed(error)
            self.pid_controls.apply(completed=applied, identifiers={action[1]})
            return
        _kind, group, requested, payload = action
        if not all(self._allowed(e) for e in self.entries if e["id"] in requested):
            self._apply_failed("Driver state changed before configuration write")
            return
        def verified(success, raw):
            try:
                values = self._decode(group, success, raw)
                for entry in self.entries:
                    identifier = entry["id"]
                    if identifier in requested and pack_value(entry, values[identifier]) != pack_value(entry, requested[identifier]):
                        raise ValueError("Configuration readback differs from the requested values")
                self.values.update(values)
                self.staged.difference_update(requested)
            except (ValueError, OverflowError) as exc:
                self._apply_failed(str(exc))
                return
            self._apply_next()
        def written(success, _raw):
            if not success:
                self._apply_failed("Driver rejected the configuration write")
            else:
                self._request(Packet(MsgType.MSG_GET_CONFIGURATION, bytes((group,))), group, verified)
        self._request(Packet(MsgType.MSG_SET_CONFIGURATION, payload), group, written)
