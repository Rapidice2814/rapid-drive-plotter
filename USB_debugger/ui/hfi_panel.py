"""Comparison-first controls for experimental encoder-seeded HFI feedback."""
import math
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDockWidget, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget
from ui.busy_button import BusyButton
from protocol_codec import Packet, StatePayload
from protocol_definitions import MsgType, FOCState
from hfi_protocol import ENABLED, ACTIVE, SEEDED, LOCKED, VALID, HfiInfo


class HfiDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("HFI (experimental)", parent)
        self.on_command = on_command
        self.supported = self.connected = False
        self.state = None
        self.info = None
        panel = QWidget()
        layout = QVBoxLayout(panel)
        help_text = QLabel("Compare first: keep Encoder feedback selected. Set a reviewed small injection amplitude and enable HFI "
                           "in Configuration → HFI, then enter zero-current open-loop RUN with the unloaded shaft clear. "
                           "RUN energizes the motor even at zero target.\n\n"
                           "Plot encoder_angle_electrical with hfi.angle_electrical, hfi.error_electrical and hfi.status. "
                           "Mechanical position and error are also available as signals.\n\n"
                           "This is low-speed, encoder-seeded HFI: saliency must be observable. It does not provide independent "
                           "absolute mechanical position or sensorless startup. Switching requires stationary zero-current open-loop RUN "
                           "and valid HFI lock. STOP/ERROR restores encoder feedback. A lost HFI lock while selected latches a fault.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.source = QComboBox()
        self.source.addItem("Encoder (default)", 0)
        self.source.addItem("HFI — experimental, encoder-seeded", 1)
        self.source.currentIndexChanged.connect(self._permissions)
        layout.addWidget(self.source)
        self.apply = BusyButton("Apply feedback source")
        self.apply.activated.connect(self.apply_source)
        layout.addWidget(self.apply)
        self.read = BusyButton("Read HFI status")
        self.read.activated.connect(self.refresh)
        layout.addWidget(self.read)
        self.stop = BusyButton("STOP / restore encoder feedback")
        self.stop.activated.connect(lambda: self.on_command(Packet(MsgType.MSG_SET_STATE, StatePayload(int(FOCState.FOC_STATE_STOP)))))
        layout.addWidget(self.stop)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.setWidget(scroll)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.visibilityChanged.connect(lambda visible: self.refresh() if visible else None)
        self.set_connected(False)

    def set_supported(self, supported):
        self.supported = supported
        if self.connected and not supported:
            self.status.setText("HFI controls require firmware USB protocol 9.")
        self._permissions()

    def set_connected(self, connected):
        self.connected = connected
        self.info = None
        self.state = None
        self.source.setCurrentIndex(0)
        self.status.setText("Not connected." if not connected else "Waiting for HFI status." if self.supported else
                            "HFI controls require firmware USB protocol 9.")
        if connected:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
        self._permissions()

    def set_state(self, state):
        self.state = state
        self._permissions()

    def _permissions(self, *_args):
        ready = self.connected and self.supported
        wants_hfi = self.source.currentData() == 1
        locked = self.info is not None and bool(self.info.flags & LOCKED) and bool(self.info.flags & VALID)
        allowed = ready and self.state == FOCState.FOC_STATE_RUN and (not wants_hfi or locked)
        reason = ("Not connected." if not self.connected else "Requires firmware USB protocol 9." if not self.supported else
                  "Feedback switching requires RUN. Start in encoder-controlled, zero-current open-loop RUN." if self.state != FOCState.FOC_STATE_RUN else
                  "HFI has no valid lock. Enable injection and inspect signal quality/error before switching.")
        self.source.setEnabled(ready)
        self.read.setEnabled(ready)
        self.stop.setEnabled(self.connected)
        self.apply.setEnabled(allowed)
        self.apply.setToolTip("Firmware additionally checks stationary shaft, zero current targets, open-loop mode and faults." if allowed else reason)
        self.source.setToolTip("Select a requested feedback source, then apply; confirmed source is shown in status." if ready else reason)
        self.read.setToolTip("Read the confirmed feedback source and HFI lock/quality." if ready else reason)

    def refresh(self):
        if self.connected and self.supported and (self.isVisible() or self.info is not None and self.info.source == 1):
            self.on_command(Packet(MsgType.MSG_GET_HFI_INFO, None))

    def apply_source(self):
        if not self.apply.isEnabled():
            return
        source = int(self.source.currentData())
        if source and QMessageBox.question(self, "Use experimental HFI feedback?",
                "Only proceed after checking HFI against the encoder. HFI will control commutation, speed and position feedback. "
                "The encoder remains required as a safety reference. Incorrect estimates can produce unintended torque. "
                "Use an unloaded motor with safe current limits. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.on_command(Packet(MsgType.MSG_SET_FEEDBACK_SOURCE, bytes((source,))))
        self.on_command(Packet(MsgType.MSG_GET_HFI_INFO, None))

    def on_info(self, info: HfiInfo):
        if not self.connected:
            return
        self.info = info
        self.status.setText(f"Confirmed feedback: {'HFI (encoder-seeded)' if info.source else 'Encoder'}\n"
                            f"Injection: {'enabled' if info.flags & ENABLED else 'disabled'}, {'active' if info.flags & ACTIVE else 'inactive'}; "
                            f"{'seeded' if info.flags & SEEDED else 'unseeded'}; {'locked' if info.flags & LOCKED else 'not locked'}; "
                            f"{'valid' if info.flags & VALID else 'not valid'}\n"
                            f"Saliency current: {info.amplitude:.5g} A; electrical error: {math.degrees(info.error):.4g}°; "
                            f"estimated mechanical speed: {info.speed:.4g} rad/s")
        self._permissions()
