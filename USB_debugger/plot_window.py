from typing import Any, Callable

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QMainWindow

from protocol_codec import (
    CanHeartbeatPayload,
    ControlModePayload,
    ErrorFlagsPayload,
    LogPayload,
    MaskPayload,
    NodeIdPayload,
    PIDPayload,
    Packet,
    StatePayload,
    TextPayload,
    VarPayload,
    VersionPayload,
)
from protocol_definitions import MsgType

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


class PlotWindow(QMainWindow):
    reply_received = Signal(object)
    serial_disconnected = Signal(object)
    transport_ready = Signal()

    def __init__(self):
        super().__init__()

        self.on_command: Callable[[Packet], None] = lambda _pkt: None
        self.start_connection_callback: Callable[[str], None] | None = None
        self.logging_toggle_callback: Callable[[bool], None] | None = None
        self.worker: Any | None = None
        self._transport_ready = False

        self.setWindowTitle("Serial Plotter")
        self.resize(1400, 900)

        self._build_ui()
        # Qt queues these calls when emitted from the serial worker thread.
        self.reply_received.connect(self.on_reply)
        self.serial_disconnected.connect(self._handle_serial_disconnected)
        self.transport_ready.connect(self._handle_transport_ready)

    def set_command_sender(self, sender: Callable[[Packet], None]):
        self.on_command = sender
        self._refresh_command_targets()

    def set_start_connection_callback(self, callback: Callable[[str], None]):
        self.start_connection_callback = callback
        if hasattr(self, "connect_dock"):
            self.connect_dock.on_connect = callback

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

    def _build_ui(self):
        self.plot_panel = PlotPanel(self)
        self.plot_panel.start_stop_button.clicked.connect(self._toggle_plot_logging)
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
        self.control_dock.btn_start.clicked.connect(self._on_controls_start_logging)
        self.control_dock.btn_stop.clicked.connect(self._on_controls_stop_logging)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.control_dock)

        self.var_dock = VarDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.var_dock)

        self.basics_dock = BasicsDock(self.on_command, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.basics_dock)

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
        self.basics_dock.visibilityChanged.connect(self._on_basics_visibility_changed)
        self.pid_dock.visibilityChanged.connect(self._on_pid_visibility_changed)
        self.var_dock.visibilityChanged.connect(self._on_variables_visibility_changed)
        self.can_dock.visibilityChanged.connect(self._on_can_visibility_changed)
        self.basics_dock.raise_()

    def _on_connect_clicked(self, port: str):
        if self.worker is not None:
            return

        if self.start_connection_callback is not None:
            self._transport_ready = False
            self.plot_panel.clear_data()
            self.plot_panel.set_transport_connected(False)
            self.plot_panel.set_logging_active(False)
            self.basics_dock.begin_connection()
            self.pid_dock.begin_connection()
            self.var_dock.begin_connection()
            self.setpoint_dock.begin_connection()
            self.can_dock.begin_connection()
            self.start_connection_callback(port)
        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(
                True, simulator=(port == SIMULATOR_ENDPOINT)
            )

    def _on_disconnect_clicked(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

        if hasattr(self, "connect_dock"):
            self.connect_dock.set_connected(False)
        self._transport_ready = False
        self.plot_panel.set_transport_connected(False)
        self.plot_panel.set_logging_active(False)
        self.basics_dock.set_disconnected()
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
            self._transport_ready = False
            self.plot_panel.set_transport_connected(False)
            self.plot_panel.set_logging_active(False)
            self.basics_dock.set_disconnected()
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
        self._transport_ready = False
        self.worker = None
        self.connect_dock.set_connected(False)
        self.plot_panel.set_transport_connected(False)
        self.plot_panel.set_logging_active(False)
        self.basics_dock.set_disconnected()
        self.pid_dock.set_disconnected()
        self.var_dock.set_disconnected()
        self.setpoint_dock.set_disconnected()
        self.can_dock.set_disconnected()

    @Slot()
    def _handle_transport_ready(self) -> None:
        self._transport_ready = True
        self.plot_panel.set_transport_connected(True)
        # Connection initialization starts telemetry logging after the port is ready.
        self.plot_panel.set_logging_active(True)
        self.basics_dock.transport_ready()
        self.pid_dock.transport_ready()
        self.var_dock.transport_ready()
        self.setpoint_dock.transport_ready()
        self.can_dock.transport_ready()
        self._refresh_visible_data_tabs()

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
            packet.msg_type == MsgType.MSG_VERSION_REPLY
            and isinstance(packet.data, VersionPayload)
        ):
            self.basics_dock.set_version(
                packet.data.major, packet.data.minor, packet.data.patch
            )

        elif (
            packet.msg_type == MsgType.MSG_STATE_REPLY
            and isinstance(packet.data, StatePayload)
        ):
            self.basics_dock.set_state(packet.data.state)
            self.pid_dock.set_driver_state(packet.data.state)
            self.var_dock.set_driver_state(packet.data.state)
            self.setpoint_dock.set_driver_state(packet.data.state)

        elif packet.msg_type == MsgType.MSG_MASK_REPLY and isinstance(packet.data, MaskPayload):
            self.signal_dock.set_mask(packet.data.mask)

        elif packet.msg_type == MsgType.MSG_NODE_ID_REPLY and isinstance(packet.data, NodeIdPayload):
            self.can_dock.set_node_id_value(packet.data.node_id)

        elif packet.msg_type == MsgType.MSG_CAN_HEARTBEAT_REPLY and isinstance(packet.data, CanHeartbeatPayload):
            self.can_dock.set_heartbeat_value(packet.data.rate_ms)

        elif packet.msg_type == MsgType.MSG_CONTROL_MODE_REPLY and isinstance(packet.data, ControlModePayload):
            self.basics_dock.set_control_mode(packet.data.mode)
            self.setpoint_dock.set_control_mode(packet.data.mode)

        elif packet.msg_type == MsgType.MSG_ACTIVE_ERRORS_REPLY and isinstance(packet.data, ErrorFlagsPayload):
            self.basics_dock.set_active_errors(packet.data.value)

        elif packet.msg_type == MsgType.MSG_LATCHED_ERRORS_REPLY and isinstance(packet.data, ErrorFlagsPayload):
            self.basics_dock.set_latched_errors(packet.data.value)

        elif (
            packet.msg_type == MsgType.MSG_TEXT_REPLY
            and isinstance(packet.data, TextPayload)
        ):
            self.enqueue_text(packet.data.text)

        elif packet.msg_type == MsgType.MSG_PID_REPLY and isinstance(packet.data, PIDPayload):
            if packet.data.kp is not None and packet.data.ki is not None and packet.data.kd is not None:
                self.enqueue_text(str(packet.data))
                self.pid_dock.set_pid_values(
                    packet.data.controller_id,
                    packet.data.kp,
                    packet.data.ki,
                    packet.data.kd,
                )

        elif packet.msg_type == MsgType.MSG_VAR_REPLY and isinstance(packet.data, VarPayload):
            if packet.data.value is not None:
                self.enqueue_text(str(packet.data))
                self.var_dock.set_var_value(packet.data.var_id, packet.data.value)
                self.setpoint_dock.set_var_value(packet.data.var_id, packet.data.value)

        elif isinstance(packet.data, bytes):
            # Keep acknowledgments and device errors visible in the terminal.
            if packet.data:
                self.enqueue_text(f"{packet.msg_type.name}: {packet.data.hex(' ')}")
            else:
                self.enqueue_text(packet.msg_type.name)

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
        self.plot_panel.set_logging_active(active)

    def _on_controls_start_logging(self):
        if self._transport_ready:
            self.plot_panel.set_logging_active(True)

    def _on_controls_stop_logging(self):
        if self._transport_ready:
            self.plot_panel.set_logging_active(False)

    def _handle_text_command(self, cmd: str):
        cmd = cmd.strip()
        if not cmd:
            return

        text_payload = TextPayload(text=cmd)
        pkg = Packet(msg_type=MsgType.MSG_TEXT_COMMAND, data=text_payload)
        self.on_command(pkg)

    def _back_to_home(self):
        self.plot_panel.reset_zoom()