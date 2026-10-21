from typing import Any, Callable, Protocol

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QMainWindow
from shiboken6 import isValid

from protocol_codec import (
    CanCyclicRatePayload,
    ControlModePayload,
    CommandErrorPayload,
    ErrorFlagsPayload,
    EncoderInfoPayload,
    LogPayload,
    MaskPayload,
    NodeIdPayload,
    PIDPayload,
    Packet,
    StatePayload,
    TextPayload,
    VarPayload,
)
from protocol_definitions import MsgType
from device_definitions import DeviceInfo

from serial_worker import SerialWorker
from config import SIMULATOR_ENDPOINT
from ui.command_panel import CommandDock
from ui.pid_panel import PidDock
from ui.signal_selector_panel import SignalSelectorDock
from ui.plot_panel import PlotPanel
from ui.control_panel import ControlDock
from ui.variable_panel import VarDock
from ui.connect_panel import SerialConnectDock
from ui.basics_panel import BasicsDock
from ui.setpoint_panel import SetpointDock
from ui.can_panel import CanInterfaceDock
from ui.busy_button import BusyButton
from ui.calibration_panel import CalibrationDock
from ui.configuration_panel import ConfigurationDock
from ui.hfi_panel import HfiDock
from hfi_protocol import HfiInfo
from ui.disabled_tooltips import DisabledTooltips
from calibration_protocol import CalibrationInfo, CalibrationTable


class CommandSender(Protocol):
    def __call__(self, packet: Packet, *, completed: Callable[[bool], None] | None = None) -> None: ...


def _discard_command(packet: Packet, *, completed: Callable[[bool], None] | None = None) -> None:
    if completed is not None:
        completed(False)


class PlotWindow(QMainWindow):
    reply_received = Signal(object)
    serial_disconnected = Signal(object)
    transport_ready = Signal(float)
    logging_confirmed = Signal(bool)
    definitions_received = Signal(object)
    connection_ready = Signal(object)
    command_finished = Signal(object)

    def __init__(self):
        super().__init__()

        self._command_sender: CommandSender = _discard_command
        self._supports_completion = False
        self.on_command: Callable[[Packet], None] = self._send_command
        self.start_connection_callback: Callable[[str], None] | None = None
        self.logging_toggle_callback: Callable[[bool], None] | None = None
        self.worker: Any | None = None
        self._transport_ready = False

        self.setWindowTitle("Serial Plotter")
        self.resize(1400, 900)

        self._build_ui()
        self._disabled_tooltips = DisabledTooltips(self)
        # Qt queues these calls when emitted from the serial worker thread.
        self.reply_received.connect(self.on_reply)
        self.serial_disconnected.connect(self._handle_serial_disconnected)
        self.transport_ready.connect(self._handle_transport_ready)
        self.logging_confirmed.connect(self._handle_logging_confirmed)
        self.definitions_received.connect(self._handle_definitions)
        self.connection_ready.connect(self._handle_connection_ready)
        self.command_finished.connect(self._handle_command_finished)

    def set_command_sender(self, sender: Callable[..., None], *, supports_completion: bool = False):
        if supports_completion:
            self._command_sender = sender
        else:
            def send_without_completion(packet: Packet, *, completed: Callable[[bool], None] | None = None) -> None:
                sender(packet)
            self._command_sender = send_without_completion
        self._supports_completion = supports_completion
        self._refresh_command_targets()

    def _send_command(self, packet: Packet):
        button = BusyButton.current() if self._supports_completion else None
        generation = button.begin_busy() if button is not None else None
        try:
            if button is None:
                self._command_sender(packet)
            else:
                self._command_sender(packet, completed=lambda success:
                                     self.command_finished.emit((button, generation, success)))
        except (ValueError, TypeError, RuntimeError) as exc:
            if button is not None:
                button.end_busy(generation)
            self.enqueue_text(f"Could not send {packet.msg_type.name}: {exc}")

    @Slot(object)
    def _handle_command_finished(self, result):
        button, generation, _success = result
        if isValid(button):
            button.end_busy(generation)

    def _reset_busy_buttons(self):
        for button in self.findChildren(BusyButton):
            button.reset_busy()

    def set_start_connection_callback(self, callback: Callable[[str], None]):
        self.start_connection_callback = callback
        if hasattr(self, "connect_dock"):
            self.connect_dock.on_connect = self._on_connect_clicked

    def set_logging_toggle_callback(self, callback: Callable[[bool], None]):
        self.logging_toggle_callback = callback

    def _on_logging_toggled(self, enabled: bool):
        if self.logging_toggle_callback is not None:
            self.logging_toggle_callback(enabled)

    def _refresh_command_targets(self):
        if hasattr(self, "signal_dock"):
            self.signal_dock.on_command = self.on_command
        if hasattr(self, "pid_dock"):
            self.pid_dock.on_command = self.on_command
        if hasattr(self, "control_dock"):
            self.control_dock.on_command = self.on_command
        if hasattr(self, "var_dock"):
            self.var_dock.on_command = self.on_command
        if hasattr(self, "basics_dock"):
            self.basics_dock.on_command = self.on_command
        if hasattr(self, "setpoint_dock"):
            self.setpoint_dock.on_command = self.on_command
        if hasattr(self, "can_dock"):
            self.can_dock.on_command = self.on_command
        if hasattr(self, "hfi_dock"):
            self.hfi_dock.on_command = self.on_command

    def _build_ui(self):
        self.plot_panel = PlotPanel(self)
        self.plot_panel.start_stop_button.activated.connect(self._toggle_plot_logging)
        self.setCentralWidget(self.plot_panel)

        self.command_dock = CommandDock(self._handle_text_command, self)

        self.connect_dock = SerialConnectDock(
            self._on_connect_clicked,
            self._on_disconnect_clicked,
            self,
        )

        self.connect_dock.save_log_checkbox.toggled.connect(self._on_logging_toggled)

        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.command_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.connect_dock)
        self.splitDockWidget(self.command_dock, self.connect_dock, Qt.Orientation.Horizontal)

        self.resizeDocks(
            [self.command_dock, self.connect_dock],
            [8, 1],
            Qt.Orientation.Horizontal,
        )


        self.signal_dock = SignalSelectorDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.signal_dock)

        self.pid_dock = PidDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.pid_dock)

        self.control_dock = ControlDock(
            on_command=self.on_command,
            on_plot_reset=self._back_to_home,
            parent=self,
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.control_dock)

        self.var_dock = VarDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.var_dock)

        self.basics_dock = BasicsDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.basics_dock)
        self.calibration_dock = CalibrationDock(self.basics_dock,
            lambda packet, completed=None: self._command_sender(packet, completed=completed), self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.calibration_dock)
        self.configuration_dock = ConfigurationDock(
            lambda packet, completed=None: self._command_sender(packet, completed=completed), self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.configuration_dock)
        self.hfi_dock = HfiDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.hfi_dock)

        self.setpoint_dock = SetpointDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.setpoint_dock)

        self.can_dock = CanInterfaceDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.can_dock)

        # Anchor the tab group on Basics so it appears first and is selected
        # when the application opens.
        self.tabifyDockWidget(self.basics_dock, self.setpoint_dock)
        self.tabifyDockWidget(self.basics_dock, self.control_dock)
        self.tabifyDockWidget(self.basics_dock, self.pid_dock)
        self.tabifyDockWidget(self.basics_dock, self.var_dock)
        self.tabifyDockWidget(self.basics_dock, self.can_dock)
        self.tabifyDockWidget(self.basics_dock, self.calibration_dock)
        self.tabifyDockWidget(self.basics_dock, self.configuration_dock)
        self.tabifyDockWidget(self.basics_dock, self.hfi_dock)
        self.basics_dock.visibilityChanged.connect(self._on_basics_visibility_changed)
        self.pid_dock.visibilityChanged.connect(self._on_pid_visibility_changed)
        self.var_dock.visibilityChanged.connect(self._on_variables_visibility_changed)
        self.can_dock.visibilityChanged.connect(self._on_can_visibility_changed)
        self.basics_dock.raise_()

    def _on_connect_clicked(self, port: str):
        if self.worker is not None:
            return

        if self.start_connection_callback is not None:
            self.connect_dock.connect_btn.begin_busy()
            self._transport_ready = False
            self.plot_panel.clear_data()
            self.plot_panel.set_transport_connected(False)
            self.plot_panel.set_logging_active(False)
            self.basics_dock.begin_connection()
            self.pid_dock.begin_connection()
            self.var_dock.begin_connection()
            self.setpoint_dock.begin_connection()
            self.can_dock.begin_connection()
            try:
                self.start_connection_callback(port)
            except (OSError, ValueError, RuntimeError) as exc:
                self.connect_dock.connect_btn.reset_busy()
                self.connect_dock.set_connected(False)
                self.enqueue_text(f"Connection failed: {exc}")
                return
        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(
                True, simulator=(port == SIMULATOR_ENDPOINT)
            )

    def _on_disconnect_clicked(self):
        self._reset_busy_buttons()
        self.configuration_dock.set_connected(False)
        self._clear_definitions()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(False)
        self._transport_ready = False
        self.plot_panel.set_transport_connected(False)
        self.plot_panel.set_logging_active(False)
        self.basics_dock.set_disconnected()
        self.calibration_dock.set_connected(False)
        self.pid_dock.set_disconnected()
        self.var_dock.set_disconnected()
        self.setpoint_dock.set_disconnected()
        self.can_dock.set_disconnected()

    def set_worker(self, worker: Any | None):
        self.worker = worker
        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(
                worker is not None,
                simulator=bool(getattr(worker, "is_simulator", False)),
            )
        if worker is None and hasattr(self, "plot_panel"):
            self._reset_busy_buttons()
            self._clear_definitions()
            self._transport_ready = False
            self.plot_panel.set_transport_connected(False)
            self.plot_panel.set_logging_active(False)
            self.basics_dock.set_disconnected()
            self.calibration_dock.set_connected(False)
            self.pid_dock.set_disconnected()
            self.var_dock.set_disconnected()
            self.setpoint_dock.set_disconnected()
            self.can_dock.set_disconnected()

    def enqueue_log(self, payload: LogPayload):
        self.plot_panel.enqueue_log(payload)

    def enqueue_text(self, payload: str):
        self.command_dock.enqueue_text(payload)

    def enqueue_reply(self, packet: Packet) -> None:
        """Deliver a decoded reply on the GUI thread, even if called by a worker."""
        self.reply_received.emit(packet)

    @Slot(object)
    def _handle_serial_disconnected(self, disconnected_worker: Any) -> None:
        # Ignore delayed disconnect notifications from a worker that has already
        # been stopped or replaced by a newer connection.
        if disconnected_worker is not self.worker:
            return
        self._reset_busy_buttons()
        self._clear_definitions()
        self._transport_ready = False
        self.worker = None
        self.connect_dock.set_connected(False)
        self.plot_panel.set_transport_connected(False)
        self.plot_panel.set_logging_active(False)
        self.basics_dock.set_disconnected()
        self.calibration_dock.set_connected(False)
        self.pid_dock.set_disconnected()
        self.var_dock.set_disconnected()
        self.setpoint_dock.set_disconnected()
        self.can_dock.set_disconnected()

    @Slot(float)
    def _handle_transport_ready(self, sample_rate: float) -> None:
        self.connect_dock.connect_btn.reset_busy()
        self.plot_panel.sample_rate = sample_rate
        self._transport_ready = True
        self.signal_dock.set_connected(True)
        self.plot_panel.set_transport_connected(True)
        # Connection initialization starts telemetry logging after the port is ready.
        self.plot_panel.set_logging_active(True)
        self.basics_dock.transport_ready()
        self.calibration_dock.set_connected(True)
        self.pid_dock.transport_ready()
        self.var_dock.transport_ready()
        self.setpoint_dock.transport_ready()
        self.can_dock.transport_ready()
        self.configuration_dock.set_connected(True)
        self.hfi_dock.set_connected(True)
        self._refresh_visible_data_tabs()

    @Slot(object)
    def _handle_connection_ready(self, message):
        worker, sample_rate = message
        if worker is self.worker and worker is not None:
            self.transport_ready.emit(sample_rate)

    @Slot(object)
    def _handle_definitions(self, message):
        definitions, info, mask, worker = message
        if worker is not self.worker or worker is None:
            return
        self.signal_dock.set_definitions(definitions, info, mask)
        self.pid_dock.set_definitions(definitions.pids)
        self.var_dock.set_definitions(definitions.variables)
        self.setpoint_dock.set_definitions(definitions.variables)
        self.setpoint_dock.set_validated_api(info.protocol >= 8)
        self.var_dock.set_debug_writes(info.debug_writes)
        self.configuration_dock.set_definitions(definitions.configuration)
        self.hfi_dock.set_supported(info.protocol >= 9)
        self.can_dock.set_definitions(definitions.can_cyclic, info.sample_rate)
        self.calibration_dock.set_definitions(definitions.variables, definitions.pids, definitions.configuration)
        self.basics_dock.validated_targets = info.protocol >= 8
        self.basics_dock.variable_ids = {entry["name"]: entry["id"] for entry in definitions.variables}
        self.basics_dock.set_version(*info.firmware)

    def _clear_definitions(self):
        self.hfi_dock.set_connected(False)
        self.hfi_dock.set_supported(False)
        self.configuration_dock.set_connected(False)
        self.configuration_dock.set_definitions(None)
        self.setpoint_dock.set_validated_api(False)
        self.var_dock.set_debug_writes(False)
        self.signal_dock.clear_definitions()
        self.pid_dock.set_definitions([])
        self.var_dock.set_definitions([])
        self.setpoint_dock.set_definitions([])
        self.can_dock.clear_definitions()
        self.calibration_dock.set_definitions([], [])
        self.basics_dock.variable_ids = {}
        self.basics_dock.validated_targets = False

    @Slot(bool)
    def _on_basics_visibility_changed(self, visible: bool) -> None:
        if visible and self._transport_ready:
            self.basics_dock.request_errors()

    @Slot(bool)
    def _on_pid_visibility_changed(self, visible: bool) -> None:
        if visible and self._transport_ready:
            self.pid_dock._request_all_pid_values()

    @Slot(bool)
    def _on_variables_visibility_changed(self, visible: bool) -> None:
        if visible and self._transport_ready:
            self.var_dock._request_all_var_values()

    @Slot(bool)
    def _on_can_visibility_changed(self, visible: bool) -> None:
        if visible and self._transport_ready:
            self.can_dock.request_all()

    def _refresh_visible_data_tabs(self) -> None:
        # A tab may already be selected while a connection is opening; read it
        # once the transport becomes ready even if no visibility signal follows.
        if self.pid_dock.isVisible():
            self.pid_dock._request_all_pid_values()
        if self.var_dock.isVisible():
            self.var_dock._request_all_var_values()
        if self.can_dock.isVisible():
            self.can_dock.request_all()

    @Slot(object)
    def on_reply(self, packet: Packet):
        if (
            packet.msg_type == MsgType.MSG_DEVICE_INFO_REPLY
            and isinstance(packet.data, DeviceInfo)
        ):
            self.basics_dock.set_version(
                *packet.data.firmware
            )

        elif (
            packet.msg_type == MsgType.MSG_STATE_REPLY
            and isinstance(packet.data, StatePayload)
        ):
            self.basics_dock.set_state(packet.data.state)
            self.configuration_dock.set_state(packet.data.state)
            self.hfi_dock.set_state(packet.data.state)
            self.calibration_dock.set_state(packet.data.state)
            self.pid_dock.set_driver_state(packet.data.state)
            self.var_dock.set_driver_state(packet.data.state)
            self.setpoint_dock.set_driver_state(packet.data.state)

        elif packet.msg_type == MsgType.MSG_HFI_INFO_REPLY and isinstance(packet.data, HfiInfo):
            self.hfi_dock.on_info(packet.data)

        elif packet.msg_type == MsgType.MSG_CONFIGURATION_REPLY and isinstance(packet.data, bytes):
            self.configuration_dock.on_reply(packet.data)
            self.calibration_dock.on_setup_configuration(packet.data)

        elif packet.msg_type == MsgType.MSG_TARGETS_REPLY and isinstance(packet.data, bytes):
            self.setpoint_dock.set_target_values(packet.data)

        elif packet.msg_type == MsgType.MSG_MASK_REPLY and isinstance(packet.data, MaskPayload):
            self.signal_dock.set_mask(packet.data.mask)

        elif packet.msg_type == MsgType.MSG_NODE_ID_REPLY and isinstance(packet.data, NodeIdPayload):
            self.can_dock.set_node_id_value(packet.data.node_id)

        elif packet.msg_type == MsgType.MSG_CAN_CYCLIC_REPLY and isinstance(packet.data, CanCyclicRatePayload):
            self.can_dock.set_cyclic_rate_value(
                packet.data.cyclic_type,
                packet.data.rate_cycles,
            )

        elif packet.msg_type == MsgType.MSG_CALIBRATION_INFO_REPLY and isinstance(packet.data, CalibrationInfo):
            self.calibration_dock.on_info(packet.data)
        elif packet.msg_type == MsgType.MSG_CALIBRATION_TABLE_REPLY and isinstance(packet.data, CalibrationTable):
            self.calibration_dock.on_table(packet.data)
        elif packet.msg_type == MsgType.MSG_ENCODER_INFO_REPLY and isinstance(packet.data, EncoderInfoPayload):
            self.basics_dock.set_encoder_info(packet.data)

        elif packet.msg_type == MsgType.MSG_CONTROL_MODE_REPLY and isinstance(packet.data, ControlModePayload):
            self.basics_dock.set_control_mode(packet.data.mode)
            self.setpoint_dock.set_control_mode(packet.data.mode)

        elif packet.msg_type == MsgType.MSG_ACTIVE_ERRORS_REPLY and isinstance(packet.data, ErrorFlagsPayload):
            self.basics_dock.set_active_errors(packet.data.value)
            self.calibration_dock.set_errors(active=packet.data.value)

        elif packet.msg_type == MsgType.MSG_LATCHED_ERRORS_REPLY and isinstance(packet.data, ErrorFlagsPayload):
            self.basics_dock.set_latched_errors(packet.data.value)
            self.calibration_dock.set_errors(latched=packet.data.value)

        elif (
            packet.msg_type == MsgType.MSG_TEXT_REPLY
            and isinstance(packet.data, TextPayload)
        ):
            self.enqueue_text(packet.data.text)

        elif packet.msg_type == MsgType.MSG_PID_REPLY and isinstance(packet.data, PIDPayload):
            self.calibration_dock.on_setup_pid(packet.data)
            if packet.data.kp is not None and packet.data.ki is not None and packet.data.kd is not None:
                self.pid_dock.set_pid_values(
                    packet.data.controller_id,
                    packet.data.kp,
                    packet.data.ki,
                    packet.data.kd,
                )

        elif packet.msg_type == MsgType.MSG_VAR_REPLY and isinstance(packet.data, VarPayload):
            self.calibration_dock.on_setup_variable(packet.data)
            if packet.data.value is not None:
                self.var_dock.set_var_value(packet.data.var_id, packet.data.value)
                self.setpoint_dock.set_var_value(packet.data.var_id, packet.data.value)

        elif isinstance(packet.data, CommandErrorPayload):
            # The session callback displays the command-specific explanation.
            return

    def _toggle_plot_logging(self):
        if not self._transport_ready:
            return
        self._set_plot_logging_active(
            not self.plot_panel.logging_active,
            send_command=True,
        )

    def _set_plot_logging_active(self, active: bool, *, send_command: bool = False):
        active = bool(active)
        if send_command and self._transport_ready:
            msg_type = MsgType.MSG_START_LOG if active else MsgType.MSG_STOP_LOG
            self.on_command(Packet(msg_type=msg_type, data=None))
            return
        self.plot_panel.set_logging_active(active)

    @Slot(bool)
    def _handle_logging_confirmed(self, active: bool):
        if self._transport_ready:
            self.plot_panel.set_logging_active(active)

    def _handle_text_command(self, cmd: str):
        cmd = cmd.strip()
        if not cmd:
            return

        text_payload = TextPayload(text=cmd)
        pkg = Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=text_payload)
        self.on_command(pkg)

    def _back_to_home(self):
        self.plot_panel.reset_zoom()
