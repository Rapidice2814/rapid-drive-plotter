"""Motor commissioning UI with correlated completion and fail-stop sequencing."""
import struct
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QMessageBox, QProgressBar, QSizePolicy)
from ui.configuration_panel import ConfigurationDock
from protocol_codec import Packet, StatePayload, EncoderSourcePayload, PIDPayload
from protocol_definitions import MsgType, FOCState

class SetupPanel(QWidget):
    STEPS = (
        ("Detect encoder direction and pole pairs", FOCState.FOC_STATE_MOTOR_PROBE, None),
        ("Align encoder", FOCState.FOC_STATE_ALIGNMENT, None),
        ("Measure L/R", FOCState.FOC_STATE_IDENTIFY, None),
        ("Tune PI controllers", FOCState.FOC_STATE_PID_AUTOTUNE, None),
        ("Calibrate encoder", FOCState.FOC_STATE_ENCODER_CALIBRATION, 0),
        ("Calibrate anti-cogging", FOCState.FOC_STATE_ANTICOGGING, 1),
    )

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.connected = False
        self.state = None
        self.faults = None
        self.supported = None
        self.running = False
        self.pending_poll = False
        self.token = 0
        self.todo = []
        self.current = None
        self.baseline = None
        self.phase = "idle"
        self.ack_source = None
        self.source_valid = False
        self.voltage_dirty = False
        self.gains_dirty = False
        layout = QVBoxLayout(self)
        self.status = QLabel("Connect to read motor setup status.")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self.status)
        self.step_progress = QProgressBar()
        self.step_progress.setRange(0,1000); self.step_progress.hide()
        layout.addWidget(self.step_progress)
        manual = QGroupBox("1. Review and apply manual settings before starting")
        manual_layout = QVBoxLayout(manual)
        explanation = QLabel("Select the physical encoder. Set safe motor/driver current and voltage limits, bus/temperature trips, "
                             "current-control bandwidth and encoder calibration D current. Pole pairs, direction and R/L are detected below. "
                             "All settings here duplicate the other pages. Reads do not apply edits. Configuration changes apply to RAM; save separately.")
        explanation.setWordWrap(True)
        explanation.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        manual_layout.addWidget(explanation)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Physical encoder"))
        self.source = QComboBox()
        self.source.activated.connect(self._source_changed)
        source_row.addWidget(self.source)
        manual_layout.addLayout(source_row)
        voltage_row = QHBoxLayout()
        voltage_row.addWidget(QLabel("Detection/alignment excitation"))
        self.voltage = QDoubleSpinBox()
        self.voltage.setDecimals(3); self.voltage.setRange(0.001, 30); self.voltage.setValue(0.5); self.voltage.setSuffix(" V")
        self.voltage.valueChanged.connect(lambda _: setattr(self, "voltage_dirty", True))
        voltage_row.addWidget(self.voltage)
        self.apply_voltage = QPushButton("Apply setup voltage (RAM only)")
        self.apply_voltage.clicked.connect(self._apply_voltage)
        voltage_row.addWidget(self.apply_voltage)
        manual_layout.addLayout(voltage_row)
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel("Optional anti-cogging PID"))
        self.anti_gains = []
        for name, value in (("Kp",0), ("Ki",0), ("Kd",0)):
            gain_row.addWidget(QLabel(name))
            field = QDoubleSpinBox(); field.setDecimals(6); field.setRange(0,10000); field.setValue(value)
            field.valueChanged.connect(lambda _: setattr(self, "gains_dirty", True))
            self.anti_gains.append(field); gain_row.addWidget(field)
        self.read_gains = QPushButton("Read gains")
        self.read_gains.clicked.connect(lambda: owner.read_anticog_pid())
        self.apply_gains = QPushButton("Apply gains")
        self.apply_gains.clicked.connect(self._apply_gains)
        gain_row.addWidget(self.read_gains); gain_row.addWidget(self.apply_gains)
        manual_layout.addLayout(gain_row)
        self.configuration = ConfigurationDock(lambda packet, completed=None, received=None: owner._send(packet, completed, received=received), self)
        self.configuration.setTitleBarWidget(QWidget())
        self.configuration.setMinimumHeight(240); self.configuration.setMaximumHeight(390)
        manual_layout.addWidget(self.configuration)
        layout.addWidget(manual)
        controls = QHBoxLayout()
        self.run_all = QPushButton("Run selected steps")
        self.run_all.clicked.connect(lambda: self.begin([i for i,c in enumerate(self.checks) if c.isChecked()]))
        self.stop = QPushButton("Stop setup / cancel motor operation")
        self.stop.clicked.connect(self.cancel)
        controls.addWidget(self.run_all); controls.addWidget(self.stop)
        self.clear_faults = QPushButton("Clear latched faults")
        self.clear_faults.clicked.connect(lambda: self.owner._send(Packet(MsgType.MSG_CLEAR_LATCHED_ERRORS, None), lambda success: self.poll()))
        controls.addWidget(self.clear_faults)
        layout.insertLayout(1, controls)
        self.checks, self.buttons, self.results = [], [], []
        for i, (name, _state, _kind) in enumerate(self.STEPS):
            row = QHBoxLayout()
            check = QCheckBox(name); check.setChecked(i != len(self.STEPS)-1)
            button = QPushButton("Run step")
            button.setFixedWidth(110)
            button.clicked.connect(lambda _checked=False, index=i: self.begin([index]))
            result = QLabel("Not run")
            row.addWidget(check,1); row.addWidget(button); row.addWidget(result,1)
            self.checks.append(check); self.buttons.append(button); self.results.append(result)
            layout.addLayout(row)
        self.details = QLabel("Driver results and failure reasons will appear here.")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self.details)
        note = QLabel("Anti-cogging is optional and unchecked by default. Newly calibrated tables remain disabled until explicitly enabled. "
                      "Encoder calibration saves settings to flash; use Save settings to persist later anti-cogging results. "
                      "Run selected steps stops on rejection, fault, cancellation, disconnect or a failed result. It never resumes automatically.")
        note.setWordWrap(True); note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum); layout.addWidget(note)
        layout.addStretch()
        self.timer = QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self.poll)
        self.permissions()

    def set_definitions(self, entries, pids=None):
        self.configuration.set_definitions(entries, pids)
        descriptor = next((e for e in pids or [] if e["name"] == "pid_anticogging"), None)
        if descriptor is not None:
            for field in self.anti_gains:
                field.setRange(float(descriptor["minimum"]), float(descriptor["maximum"]))
        if self.connected:
            self.configuration.set_connected(True)
            self.configuration.set_state(self.state)

    def set_connected(self, connected):
        self.token += 1
        self.running = False; self.todo = []; self.current = None; self.phase = "idle"
        self.connected = connected; self.pending_poll = False; self.supported = None
        self.voltage_dirty = False
        self.gains_dirty = False
        if connected:
            for i,check in enumerate(self.checks): check.setChecked(i != len(self.STEPS)-1)
            for label in self.results: label.setText("Not run")
        self.configuration.set_connected(connected)
        self.state = None; self.faults = None; self.ack_source = None; self.source_valid = False
        self.details.setText("Waiting for driver result." if connected else "Disconnected; reconnect to read the retained driver result.")
        self.status.setText("Reading setup capability/status…" if connected else "Disconnected. Setup stopped; no automatic resume.")
        if connected: self.timer.start(); self.poll()
        else: self.timer.stop()
        self.permissions()

    def set_state(self, state):
        self.state = state; self.configuration.set_state(state); self.permissions()

    def set_errors(self, active, latched):
        self.faults = None if active is None or latched is None else active | latched
        if self.running and self.faults:
            self.abort("Driver fault; setup stopped.")
        self.permissions()

    def on_encoder_info(self, info):
        self.source.clear()
        for source in info.sources:
            self.source.addItem(source.name, source.source_id)
        self.ack_source = info.selected
        self.source.setCurrentIndex(self.source.findData(info.selected))
        self.source_valid = bool(info.flags & 8) and any(s.source_id == info.selected and s.calibratable for s in info.sources)
        self.permissions()

    def _source_changed(self, _index):
        if not self.running and self.connected:
            self.source_valid = False; self.permissions()
            self.owner._send(Packet(MsgType.MSG_SET_ENCODER_SOURCE, EncoderSourcePayload(self.source.currentData())), self.owner._after_setting)

    def on_pid(self, payload):
        if payload.controller_id == self.owner.anticog_pid_id and all(v is not None for v in (payload.kp,payload.ki,payload.kd)):
            for field,value in zip(self.anti_gains,(payload.kp,payload.ki,payload.kd)):
                field.blockSignals(True); field.setValue(value); field.blockSignals(False)
            self.gains_dirty = False

    def _apply_gains(self):
        if self.apply_gains.isEnabled():
            self.owner._send(Packet(MsgType.MSG_SET_PID, PIDPayload(self.owner.anticog_pid_id,
                *(field.value() for field in self.anti_gains))), lambda success: self.owner.read_anticog_pid())

    def _apply_voltage(self):
        if not self.apply_voltage.isEnabled(): return
        token = self.token
        def done(success):
            if token != self.token: return
            self.voltage_dirty = not success
            if not success: self.status.setText("Setup voltage rejected; review the voltage limit and driver error.")
            self.poll()
        self.owner._send(Packet(MsgType.MSG_SET_SETUP_VOLTAGE, struct.pack("<f", self.voltage.value())), done)

    def permissions(self):
        safe = self.connected and self.state == FOCState.FOC_STATE_IDLE and self.faults == 0 and not self.running
        ready = safe and self.supported is True and self.source_valid
        self.step_progress.setVisible(self.running)
        self.run_all.setEnabled(ready)
        self.stop.setEnabled(self.connected)
        self.clear_faults.setEnabled(self.connected and not self.running)
        self.source.setEnabled(safe)
        self.voltage.setEnabled(safe)
        self.apply_voltage.setEnabled(safe and self.supported is True)
        self.configuration.setEnabled(not self.running)
        gains_available = getattr(self.owner, "anticog_pid_id", None) is not None
        self.read_gains.setEnabled(self.connected and gains_available and not self.running)
        self.apply_gains.setEnabled(safe and gains_available)
        for field in self.anti_gains: field.setEnabled(safe and gains_available)
        for check,button in zip(self.checks,self.buttons):
            check.setEnabled(not self.running); button.setEnabled(ready)
        if getattr(self.owner, "setup_page", None) is self:
            self.owner._permissions()

    def begin(self, indexes):
        if not self.run_all.isEnabled() or self.running: return
        if not indexes:
            self.status.setText("Select at least one step."); return
        if (self.voltage_dirty or self.configuration.busy or self.configuration.has_unapplied_changes()
                or (5 in indexes and self.gains_dirty)):
            self.status.setText("Apply or discard manual setting edits and wait for readback before starting."); return
        if not self.configuration.entries or any(e["id"] not in self.configuration.values for e in self.configuration.entries):
            self.status.setText("Read the manual configuration groups before starting."); return
        if QMessageBox.question(self, "Run motor setup", "The unloaded motor will move and may rotate several turns. "
                                "Confirm safe current/voltage limits and keep the shaft clear. Encoder calibration saves settings to flash. "
                                "Run the selected steps?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self.token += 1; self.running = True; self.todo = list(indexes); self.current = None; self.phase = "baseline"
        self.pending_poll = False
        for i,label in enumerate(self.results): label.setText("Queued" if i in indexes else "Skipped")
        self.status.setText("Reading fresh driver result before starting…")
        self.permissions(); self.poll()

    def poll(self):
        if not self.connected or self.pending_poll or self.supported is False: return
        if not self.running and self.supported is True and not self.isVisible(): return
        self.pending_poll = True
        token = self.token
        def done(success):
            if token != self.token: return
            self.pending_poll = False
            if not success:
                self.supported = False
                if self.running: self.abort("Setup status read failed; sequence stopped.")
                else: self.status.setText("Setup workflow requires updated firmware with structured setup status.")
                self.permissions()
        self.owner._send(Packet(MsgType.MSG_GET_SETUP_STATUS, None), done)

    def on_status(self, result):
        self.supported = True
        self.state = result.state; self.faults = result.active_errors | result.latched_errors
        if not self.voltage_dirty and not self.running:
            self.voltage.blockSignals(True); self.voltage.setValue(result.voltage); self.voltage.blockSignals(False)
        if not self.running and self.status.text().startswith("Reading setup"):
            self.status.setText("Faults present; review and clear faults before setup." if self.faults else
                                "Ready. Review/apply manual settings, then run selected steps.")
        if self.running:
            if self.faults or result.state == FOCState.FOC_STATE_ERROR:
                self.abort("Driver fault; setup stopped.")
            elif self.phase == "baseline":
                self.baseline = result.sequence; self.start_next()
            elif self.current is not None:
                if result.sequence == self.baseline:
                    if time.monotonic() - self.requested_at > 10: self.abort("Driver did not start the requested step.")
                elif result.sequence != ((self.baseline + 1) & 0xFFFFFFFF) or result.routine_state != int(self.STEPS[self.current][1]):
                    self.abort("Driver result belongs to a different operation; setup stopped.")
                elif result.error or result.status in (2,3):
                    self.abort("Step failed/cancelled; see Last test / calibration result.")
                elif result.status == 0 and result.state != int(self.STEPS[self.current][1]):
                    self.abort("Driver left the requested step without reporting completion.")
                elif result.status == 1:
                    if result.state == FOCState.FOC_STATE_IDLE:
                        self.results[self.current].setText("Completed")
                        self.baseline = result.sequence
                        self.owner.read_setup_values()
                        self.configuration.read_from_driver(confirm=False)
                        self.start_next()
                    elif result.state == FOCState.FOC_STATE_STOP:
                        self.results[self.current].setText("Completed; waiting for IDLE")
                    else:
                        self.abort("Unexpected driver state after completion; setup stopped.")
        self.permissions()

    def start_next(self):
        if not self.running: return
        if not self.todo:
            self.running = False; self.current = None; self.phase = "idle"
            self.status.setText("Selected setup steps completed. Review results and save settings as needed.")
            self.configuration.read_from_driver(confirm=False)
            self.permissions(); return
        self.current = self.todo.pop(0)
        name, state, kind = self.STEPS[self.current]
        self.results[self.current].setText("Running")
        self.step_progress.setRange(0,0 if kind is None else 1000)
        self.step_progress.setValue(0)
        self.status.setText(name)
        self.phase = "waiting"; self.requested_at = time.monotonic()
        token = self.token
        def done(success):
            if token != self.token: return
            if not success: self.abort("Step request rejected; setup stopped. See driver explanation.")
        packet = (Packet(MsgType.MSG_SET_STATE, StatePayload(int(state))) if kind is None else
                  Packet(MsgType.MSG_START_CALIBRATION, bytes((kind,self.owner.rounds[kind].value()))))
        self.owner._send(packet, done)
        self.permissions()

    def on_calibration_info(self, info):
        if self.running and self.current is not None and self.STEPS[self.current][2] == info.kind:
            self.step_progress.setRange(0,1000); self.step_progress.setValue(info.progress)
            self.results[self.current].setText(f"Running {info.progress / 10:.1f}%")
            self.status.setText(self.STEPS[self.current][0] + "\n" + self.owner.time_labels[info.kind].text())

    def abort(self, reason):
        self.running = False; self.todo = []; self.phase = "idle"
        if self.current is not None: self.results[self.current].setText("Stopped/failed")
        self.token += 1; self.pending_poll = False
        self.status.setText(reason)
        if self.connected:
            self.owner._send(Packet(MsgType.MSG_SET_STATE, StatePayload(int(FOCState.FOC_STATE_STOP))))
            self.owner.read_routine_result()
        self.permissions()

    def cancel(self):
        self.abort("Setup cancelled by user.")
