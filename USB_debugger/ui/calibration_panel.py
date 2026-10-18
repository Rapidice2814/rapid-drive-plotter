"""Calibration progress and explicitly downloaded, editable compensation tables."""
import math
import struct
import zlib
from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDockWidget, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget, QAbstractItemView, QHeaderView)
from calibration_protocol import CalibrationInfo, CalibrationTable, CHUNK, ERROR_NAMES, STAGES
from protocol_codec import Packet, StatePayload
from protocol_definitions import MsgType, FOCState


class TableEditor(QWidget):
    """A numeric table plus a draggable value line for the selected plotted point."""

    def __init__(self, kind: int):
        super().__init__()
        self.kind = kind
        self.values: list[float] = []
        self.dirty = False
        self._updating = False
        self.selected = (0, 0)
        layout = QVBoxLayout(self)
        self.plot = pg.PlotWidget()
        self.plot.setFixedHeight(220)
        self.plot.setLabel("bottom", "Mechanical angle", units="deg")
        self.plot.setLabel("left", "Correction" if kind == 0 else "Compensation current", units="rad" if kind == 0 else "A")
        self.plot.addLegend()
        self.curves = [self.plot.plot(pen=pg.mkPen(color, width=2), symbol="o", symbolSize=4, symbolBrush=color, symbolPen=color,
                                      name=name) for color, name in
                       (("c", "Correction"),) if kind == 0]
        if kind:
            self.curves = [self.plot.plot(pen=pg.mkPen(color, width=2), symbol="o", symbolSize=4, symbolBrush=color, symbolPen=color, name=name)
                           for color, name in (("c", "Forward"), ("m", "Reverse"))]
        for channel, curve in enumerate(self.curves):
            curve.sigPointsClicked.connect(lambda _curve, points, _event, c=channel: self.select_point(points[0].index(), c))
        self.value_line = pg.InfiniteLine(angle=0, movable=True, pen="y")
        self.value_line.sigPositionChangeFinished.connect(self._line_edited)
        self.plot.addItem(self.value_line)
        self.value_line.hide()
        layout.addWidget(self.plot)
        self.summary = QLabel("Read a table from the driver to begin.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        help_label = QLabel("Select a table cell or plotted point, then drag the yellow value line to edit.\nEncoder angles use raw sensor coordinates; anti-cogging uses aligned mechanical coordinates.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.table = QTableWidget(0, 3 if kind == 0 else 4)
        self.table.setHorizontalHeaderLabels(["Bin", "Angle (deg)", "Correction (rad)"] if kind == 0 else
                                            ["Bin", "Angle (deg)", "Forward (A)", "Reverse (A)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(180)
        self.table.setMinimumHeight(140)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.cellChanged.connect(self._cell_edited)
        self.table.currentCellChanged.connect(self._cell_selected)
        layout.addWidget(self.table)

    def set_values(self, values: list[float]):
        self._updating = True
        self.values = list(values)
        channels = self.kind + 1
        bins = len(values) // channels
        self.table.setRowCount(bins)
        for row in range(bins):
            for column, value in enumerate([str(row), f"{row * 360 / bins:.3f}"] +
                                           [f"{values[row * channels + c]:.8g}" for c in range(channels)]):
                item = QTableWidgetItem(value)
                if column < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self._updating = False
        self.dirty = False
        self.value_line.hide()
        self.redraw()

    def redraw(self):
        channels = self.kind + 1
        bins = len(self.values) // channels
        for c, curve in enumerate(self.curves):
            curve.setData([i * 360 / bins for i in range(bins)], self.values[c::channels])
        if self.values:
            rms = math.sqrt(sum(v * v for v in self.values) / len(self.values))
            peak = max(abs(v) for v in self.values)
            unit = "rad" if self.kind == 0 else "A"
            text = f"{'Unsaved local edits. ' if self.dirty else ''}RMS {rms:.6g} {unit}; peak |value| {peak:.6g} {unit}."
            if self.kind == 0:
                text += f" Peak {math.degrees(peak):.4g}°."
            self.summary.setText(text)

    def select_point(self, row: int, channel: int):
        self.table.setCurrentCell(row, channel + 2)

    def _cell_selected(self, row: int, column: int, _old_row: int, _old_column: int):
        if row < 0 or column < 2 or not self.values:
            return
        self.selected = (row, column - 2)
        self.value_line.setValue(self.values[row * (self.kind + 1) + column - 2])
        self.value_line.show()

    def _cell_edited(self, row: int, column: int):
        if self._updating or column < 2:
            return
        item = self.table.item(row, column)
        if item is None:
            return
        index = row * (self.kind + 1) + column - 2
        try:
            value = float(item.text())
            if not math.isfinite(value) or abs(value) > (0.1 if self.kind == 0 else 1e6):
                raise ValueError("Value out of range")
            struct.pack("<f", value)
        except (ValueError, OverflowError):
            self._updating = True
            item.setText(f"{self.values[index]:.8g}")
            self._updating = False
            return
        self.values[index] = value
        self.dirty = True
        self.value_line.setValue(value)
        self.redraw()

    def _line_edited(self):
        row, channel = self.selected
        item = self.table.item(row, channel + 2)
        if item is not None:
            item.setText(f"{self.value_line.value():.8g}")


class CalibrationDock(QDockWidget):
    """Coordinates one acknowledged transfer at a time without worker-thread UI access."""
    completed = Signal(object)

    def __init__(self, basics, send: Callable, parent=None):
        super().__init__("Calibration", parent)
        self.basics = basics
        self.send = send
        self.connected = False
        self.state = None
        self.info: dict[int, CalibrationInfo] = {}
        self.transfer = None
        self.generation = 0
        self.completed.connect(self._completion)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        encoder_group = basics.encoder_source_combo.parentWidget()
        layout.addWidget(encoder_group)
        basics.encoder_calibrate_button.hide()
        basics.encoder_correction_checkbox.hide()
        basics.encoder_correction_button.hide()
        basics.encoder_status_label.hide()
        self.tabs = QTabWidget()
        self.editors: list[TableEditor] = []
        self.rounds: list[QSpinBox] = []
        self.progress: list[QProgressBar] = []
        self.status: list[QLabel] = []
        self.buttons: list[list[QPushButton]] = []
        self.enabled: list[QCheckBox] = []
        for kind, name in enumerate(("Encoder", "Anti-cogging")):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            row = QHBoxLayout()
            row.addWidget(QLabel("Revolutions per direction"))
            rounds = QSpinBox()
            rounds.setRange(1, 8)
            rounds.setValue(3)
            row.addWidget(rounds)
            self.rounds.append(rounds)
            start = QPushButton("Calibrate")
            start.clicked.connect(lambda _checked=False, k=kind: self.start(k))
            row.addWidget(start)
            stop = QPushButton("Stop")
            stop.clicked.connect(lambda: basics._send_state(FOCState.FOC_STATE_STOP))
            row.addWidget(stop)
            page_layout.addLayout(row)
            progress = QProgressBar()
            progress.setRange(0, 1000)
            self.progress.append(progress)
            page_layout.addWidget(progress)
            status = QLabel("Not connected")
            status.setWordWrap(True)
            self.status.append(status)
            page_layout.addWidget(status)
            enabled = QCheckBox("Enable encoder correction" if kind == 0 else "Enable anti-cogging compensation")
            self.enabled.append(enabled)
            page_layout.addWidget(enabled)
            apply = QPushButton("Apply enable setting")
            apply.clicked.connect(lambda _checked=False, k=kind: self._setting(k))
            page_layout.addWidget(apply)
            controls = QHBoxLayout()
            read = QPushButton("Read table")
            read.clicked.connect(lambda _checked=False, k=kind: self.download(k))
            upload = QPushButton("Apply edited table to RAM")
            upload.clicked.connect(lambda _checked=False, k=kind: self.upload(k))
            controls.addWidget(read)
            controls.addWidget(upload)
            page_layout.addLayout(controls)
            editor = TableEditor(kind)
            self.editors.append(editor)
            page_layout.addWidget(editor)
            self.buttons.append([start, stop, apply, read, upload])
            self.tabs.addTab(page, name)
        layout.addWidget(self.tabs)
        self.transfer_status = QLabel("Table transfers require stationary IDLE/ERROR. Applied tables remain disabled until enabled.")
        self.transfer_status.setWordWrap(True)
        layout.addWidget(self.transfer_status)
        self.abort = QPushButton("Abort unfinished table upload")
        self.abort.clicked.connect(self.abort_upload)
        layout.addWidget(self.abort)
        self.save = QPushButton("Save all settings and tables to flash")
        self.save.clicked.connect(self.save_settings)
        layout.addWidget(self.save)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.setWidget(scroll)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.visibilityChanged.connect(lambda visible: self.refresh() if visible else None)
        self._permissions()

    def _send(self, packet, callback=None):
        generation = self.generation
        def done(success):
            self.completed.emit((generation, callback, success))
        try:
            self.send(packet, completed=done)
        except (ValueError, TypeError, RuntimeError) as exc:
            self.transfer_status.setText(f"Command could not be sent: {exc}")
            done(False)

    def _completion(self, result):
        generation, callback, success = result
        if generation != self.generation or not self.connected:
            return
        if not success:
            self.transfer = None
            self.transfer_status.setText("Command failed. See terminal. Abort an unfinished upload before retrying.")
            self._permissions()
        if callback is not None:
            callback(success)

    def set_connected(self, connected: bool):
        self.generation += 1
        self.connected = connected
        self.transfer = None
        self.info.clear()
        self.state = None
        if connected:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            for editor in self.editors:
                editor.set_values([])
                editor.summary.setText("Read a table from the driver to begin.")
            for status in self.status:
                status.setText("Not connected")
            for progress in self.progress:
                progress.setValue(0)
        self._permissions()

    def refresh(self):
        if self.connected and self.transfer is None and (self.isVisible() or any(i.flags & 4 for i in self.info.values())):
            for kind in (0, 1):
                self._send(Packet(MsgType.MSG_GET_CALIBRATION_INFO, bytes((kind,))))

    def set_state(self, state):
        self.state = state
        self._permissions()

    def _permissions(self):
        self.basics._calibration_transfer_busy = self.transfer is not None
        self.basics._refresh_encoder_controls()
        safe = self.connected and self.state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR) and self.transfer is None
        uploading = any(info.flags & 8 for info in self.info.values())
        if not self.connected:
            blocked = "Not connected. Connect to the driver first."
        elif self.transfer is not None:
            blocked = "A calibration table transfer is in progress. Wait for it to finish."
        elif uploading:
            blocked = "The driver has an unfinished table upload. Use 'Abort unfinished table upload' before continuing."
        elif self.state is None:
            blocked = "Waiting for the driver state."
        elif not safe:
            state_name = FOCState(self.state).name.removeprefix("FOC_STATE_").replace("_", " ")
            blocked = f"Driver is in {state_name}. This control requires stationary IDLE or ERROR. Stop the motor/calibration first."
        else:
            blocked = ""

        def availability(control, enabled, reason, action):
            control.setEnabled(enabled)
            control.setToolTip(action if enabled else reason)

        for kind, buttons in enumerate(self.buttons):
            info = self.info.get(kind)
            valid = info is not None and bool(info.flags & 1)
            missing_info = "Waiting for calibration status from the driver."
            invalid_table = ("The driver has no valid encoder correction table. Complete encoder calibration or upload a valid table first."
                             if kind == 0 else
                             "The driver has no valid anti-cogging table. Complete anti-cogging calibration or upload a valid table first. Encoder calibration/source/correction changes invalidate anti-cogging data.")
            availability(buttons[0], safe and not uploading and self.state == FOCState.FOC_STATE_IDLE and info is not None,
                         blocked or (missing_info if info is None else "Calibration requires fault-free IDLE, not ERROR. Clear the faults and wait for IDLE."),
                         "Start a fresh calibration. The unloaded motor rotates in both directions.")
            availability(buttons[1], self.connected, "Not connected. Connect to the driver first.",
                         "Cancel the active routine and request STOP. This is not a hardware emergency stop.")
            can_toggle = valid or (info is not None and bool(info.flags & 2))
            toggle_reason = blocked or (missing_info if info is None else invalid_table)
            availability(buttons[2], safe and not uploading and can_toggle, toggle_reason,
                         "Send the selected enable setting to the driver. A valid table is required to enable compensation.")
            availability(buttons[3], safe and not uploading and info is not None, blocked or missing_info,
                         "Download the driver's table. Existing local edits require confirmation before replacement.")
            availability(buttons[4], safe and not uploading and bool(self.editors[kind].values),
                         blocked or "No table has been downloaded. Use 'Read table' first, then edit its values.",
                         "Upload the complete edited table to RAM. Compensation remains disabled until explicitly enabled.")
            availability(self.enabled[kind], safe and not uploading and can_toggle, toggle_reason,
                         "Choose whether compensation should be enabled, then click 'Apply enable setting'.")
            availability(self.rounds[kind], safe and not uploading, blocked,
                         "Number of complete revolutions in each direction for the next calibration.")
            availability(self.editors[kind], safe and not uploading, blocked,
                         "Read a table, then edit numeric cells or drag the selected plotted value.")
        availability(self.abort, self.connected and self.transfer is None,
                     "Not connected. Connect to the driver first." if not self.connected else "A table transfer is in progress. Wait for it to finish or fail before aborting the upload.",
                     "Discard an unfinished driver-side upload. An interrupted anti-cogging table remains invalid.")
        availability(self.save, safe and not uploading, blocked,
                     "Save all current driver settings and tables to flash without resuming RUN.")

    def on_info(self, info: CalibrationInfo):
        previous = self.info.get(info.kind)
        self.info[info.kind] = info
        self.progress[info.kind].setValue(info.progress)
        self.status[info.kind].setText(f"{STAGES[info.kind][info.stage]}; {'valid table' if info.flags & 1 else 'no valid table'}; "
                                     f"{'enabled' if info.flags & 2 else 'disabled'}" +
                                     (f". {ERROR_NAMES[info.error]} (code {info.error})" if info.error else ""))
        if previous is None or (previous.flags ^ info.flags) & 2:
            self.enabled[info.kind].setChecked(bool(info.flags & 2))
        self._permissions()

    def start(self, kind):
        if not self.buttons[kind][0].isEnabled():
            return
        if QMessageBox.question(self, "Start calibration", "The unloaded motor will rotate multiple revolutions in both directions. "
                                "Keep the shaft clear. Encoder calibration saves all settings and invalidates anti-cogging. "
                                "Anti-cogging replaces its existing table; save to flash separately. Continue?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._send(Packet(MsgType.MSG_START_CALIBRATION, bytes((kind, self.rounds[kind].value()))),
                   lambda success: self._after_setting(success))

    def _after_setting(self, success):
        if success:
            self.basics.request_state()
            self.basics.request_encoder_info()
            self.refresh()

    def _setting(self, kind):
        self._send(Packet(MsgType.MSG_ENABLE_CALIBRATION, bytes((kind, int(self.enabled[kind].isChecked())))), self._after_setting)

    def download(self, kind):
        info = self.info.get(kind)
        if info is None or not self.buttons[kind][3].isEnabled():
            return
        if self.editors[kind].dirty and QMessageBox.question(self, "Discard local edits?", "Replace local edits with the driver table?") != QMessageBox.StandardButton.Yes:
            return
        self.transfer = {"mode": "read", "kind": kind, "total": info.bins * info.channels, "values": []}
        self._permissions()
        self._read_next()

    def _read_next(self):
        transfer = self.transfer
        if transfer is None:
            return
        offset = len(transfer["values"])
        count = min(CHUNK, transfer["total"] - offset)
        self._send(Packet(MsgType.MSG_GET_CALIBRATION_TABLE, struct.pack("<BHB", transfer["kind"], offset, count)))

    def on_table(self, table: CalibrationTable):
        transfer = self.transfer
        if transfer is None or transfer["mode"] != "read":
            return
        if (table.kind != transfer["kind"] or table.offset != len(transfer["values"]) or table.total != transfer["total"]):
            self.transfer = None
            self.transfer_status.setText("Table changed or reply range mismatched; read again.")
            self._permissions()
            return
        transfer["values"].extend(table.values)
        self.transfer_status.setText(f"Reading table: {len(transfer['values']) * 100 // transfer['total']}%")
        if len(transfer["values"]) == transfer["total"]:
            self.editors[table.kind].set_values(transfer["values"])
            self.transfer = None
            self.transfer_status.setText("Driver table loaded. Edits remain local until applied to RAM.")
            self._permissions()
        else:
            self._read_next()

    def upload(self, kind):
        if not self.buttons[kind][4].isEnabled():
            return
        info = self.info.get(kind)
        values = tuple(self.editors[kind].values)
        if info is None or len(values) != info.bins * info.channels:
            return
        if QMessageBox.question(self, "Apply table to driver?", "Apply the entire edited table to RAM? Compensation will be disabled. "
                                "An interrupted anti-cogging upload invalidates its old table. Save to flash separately.") != QMessageBox.StandardButton.Yes:
            return
        payload = struct.pack(f"<{len(values)}f", *values)
        self.transfer = {"mode": "write", "kind": kind, "offset": 0, "values": values, "crc": zlib.crc32(payload)}
        self._permissions()
        self._send(Packet(MsgType.MSG_BEGIN_CALIBRATION_UPLOAD, struct.pack("<BH", kind, len(values))), self._write_next)

    def _write_next(self, success):
        transfer = self.transfer
        if not success or transfer is None:
            return
        offset = transfer["offset"]
        values = transfer["values"]
        if offset == len(values):
            self._send(Packet(MsgType.MSG_COMMIT_CALIBRATION_UPLOAD, struct.pack("<BI", transfer["kind"], transfer["crc"])), self._committed)
            return
        chunk = values[offset:offset + CHUNK]
        transfer["offset"] += len(chunk)
        self.transfer_status.setText(f"Uploading table: {offset * 100 // len(values)}%")
        self._send(Packet(MsgType.MSG_WRITE_CALIBRATION_TABLE, struct.pack("<BHB", transfer["kind"], offset, len(chunk)) +
                          struct.pack(f"<{len(chunk)}f", *chunk)), self._write_next)

    def _committed(self, success):
        if not success or self.transfer is None:
            return
        self.editors[self.transfer["kind"]].dirty = False
        self.editors[self.transfer["kind"]].redraw()
        self.transfer = None
        self.transfer_status.setText("Applied to RAM; compensation is disabled. Enable explicitly and save to flash when ready.")
        self._after_setting(True)
        self._permissions()

    def abort_upload(self):
        self.generation += 1
        self.transfer = None
        self._send(Packet(MsgType.MSG_ABORT_CALIBRATION_UPLOAD, b""), self._after_setting)
        self._permissions()

    def save_settings(self):
        if not self.save.isEnabled():
            return
        if QMessageBox.question(self, "Save settings", "Write all current settings and tables to flash? "
                                "Motor output remains stopped; flash programming can pause telemetry.") == QMessageBox.StandardButton.Yes:
            # SET_STATE has no RUN continuation, unlike the legacy FLASH_SAVE command.
            self._send(Packet(MsgType.MSG_SET_STATE, StatePayload(int(FOCState.FOC_STATE_FLASH_SAVE))), self._after_setting)
