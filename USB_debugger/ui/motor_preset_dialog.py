"""Optional parameter selection for new presets and driver snapshots."""
import json
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QFormLayout,
                               QLineEdit, QCheckBox, QScrollArea, QWidget, QLabel,
                               QPlainTextEdit, QComboBox, QGroupBox)
from configuration_protocol import pack_value
from motor_presets import validate_preset
from pid_protocol import GAINS, pack_gain
from ui.number_format import decimal_text


class MotorPresetDialog(QDialog):
    def __init__(self, entries, values=None, parent=None, pid_entries=None, pid_values=None,
                 presets=None, selected_path=None, editable=True, new_name="", selection=None):
        super().__init__(parent)
        self.setWindowTitle("Save motor preset")
        self.resize(560, 650)
        layout = QVBoxLayout(self)
        self.name = QLineEdit()
        self.name.setText(new_name)
        form = QFormLayout()
        self.target = QComboBox()
        self.target.addItem("Create new user preset", None)
        for path, data in presets or []:
            self.target.addItem(f"Update: {data['name']}", path)
        form.addRow("Save to", self.target)
        form.addRow("Model / preset name", self.name)
        layout.addLayout(form)
        def target_changed():
            path = self.target.currentData()
            data = next((data for existing, data in presets or [] if existing == path), None)
            self.name.setText(data["name"] if data else new_name)
        self.target.currentIndexChanged.connect(target_changed)
        for index in range(self.target.count()):
            if selected_path is not None and self.target.itemData(index) == selected_path:
                self.target.setCurrentIndex(index)
                break
        hint = QLabel("Every parameter is optional. Only checked fields are saved. "
                      "Omitted fields are left unchanged when applying a preset. "
                      "Each PID is saved as a complete Kp/Ki/Kd set. Omitted controllers stay unchanged. "
                      "Zero motor/limit values mean unset.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.fields = []
        self.pid_groups = []
        self.json_edit = None
        if entries or pid_entries:
            panel = QWidget()
            fields = QFormLayout(panel)
            for entry in entries:
                key = entry["name"]
                include = QCheckBox(f"{entry['group']}: {entry['label']} ({entry['unit']})")
                edit = QLineEdit()
                edit.setPlaceholderText("Optional")
                if values is not None and key in values:
                    include.setChecked(selection is None or key in selection["parameters"])
                    edit.setText(decimal_text(values[key], float32=entry["type"] == "f"))
                if not editable:
                    edit.setReadOnly(True)
                    include.setEnabled(values is not None and key in values)
                fields.addRow(include, edit)
                self.fields.append((entry, include, edit))
            for entry in pid_entries or []:
                values_for_pid = (pid_values or {}).get(entry["name"], {})
                complete = all(g in values_for_pid for g in GAINS)
                group = QGroupBox(entry["name"])
                group.setCheckable(True)
                group.setChecked(complete and (selection is None or bool(selection["pids"].get(entry["name"]))))
                gains_form = QFormLayout(group)
                edits = {}
                for gain in GAINS:
                    edit = QLineEdit()
                    edit.setPlaceholderText("Not read" if not editable else "Required when this PID is checked")
                    edit.setToolTip(f"Range: {decimal_text(entry['minimum'])} to {decimal_text(entry['maximum'])}")
                    if gain in values_for_pid:
                        edit.setText(decimal_text(values_for_pid[gain], float32=True))
                    edit.setReadOnly(not editable)
                    edits[gain] = edit
                    gains_form.addRow(gain.capitalize(), edit)
                if not editable and not complete:
                    group.setEnabled(False)
                    group.setToolTip("Read all gains from the driver before saving this PID.")
                fields.addRow(group)
                self.pid_groups.append((entry, group, edits))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(panel)
            layout.addWidget(scroll)
        else:
            layout.addWidget(QLabel("Connect a driver for the parameter form, or enter parameter names and values as JSON:"))
            self.json_edit = QPlainTextEdit(json.dumps({"parameters": values or {}, "pids": pid_values or {}}, indent=2))
            layout.addWidget(self.json_edit)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        try:
            parameters, pids = {}, {}
            if self.json_edit is not None:
                content = json.loads(self.json_edit.toPlainText())
                if not isinstance(content, dict):
                    raise ValueError("Enter an object containing parameters and/or pids")
                parameters = content.get("parameters", {})
                pids = content.get("pids", {})
            for entry, include, edit in self.fields:
                if not include.isChecked():
                    continue
                value = float(edit.text()) if entry["type"] == "f" else int(edit.text())
                pack_value(entry, value)
                parameters[entry["name"]] = value
            for entry, group, edits in self.pid_groups:
                if group.isChecked():
                    gains = {gain: float(edits[gain].text()) for gain in GAINS}
                    for value in gains.values():
                        pack_gain(entry, value)
                    pids[entry["name"]] = gains
            data = validate_preset({"version": 2, "name": self.name.text(), "parameters": parameters, "pids": pids})
            for name, gains in data["pids"].items():
                if set(gains) != set(GAINS):
                    raise ValueError(f"{name}: include Kp, Ki and Kd together, or omit the controller")
            self.parameters, self.pids = data["parameters"], data["pids"]
            self.path = self.target.currentData()
        except (ValueError, OverflowError, TypeError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()
