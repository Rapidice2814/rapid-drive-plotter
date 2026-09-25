from PySide6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor


class CommandDock(QDockWidget):
    def __init__(self, on_command, parent=None):
        super().__init__("Command Line", parent)
        self.on_command = on_command
        self.auto_scroll = True

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Type command and press Enter...")
        self.command_input.returnPressed.connect(self._send_text_command)

        self.autoscroll_checkbox = QCheckBox("Auto-scroll output")
        self.autoscroll_checkbox.setChecked(True)
        self.autoscroll_checkbox.toggled.connect(self._set_autoscroll)

        self.command_log = QTextEdit()
        self.command_log.setReadOnly(True)

        layout.addWidget(QLabel("Command:"))
        layout.addWidget(self.command_input)
        layout.addWidget(self.autoscroll_checkbox)
        output_header = QHBoxLayout()
        output_header.addWidget(QLabel("Output:"))
        output_header.addStretch()
        self.clear_output_button = QPushButton("Clear output")
        self.clear_output_button.clicked.connect(lambda: self.command_log.clear())
        output_header.addWidget(self.clear_output_button)
        layout.addLayout(output_header)
        layout.addWidget(self.command_log)

        self.setWidget(panel)

    def _set_autoscroll(self, enabled: bool):
        self.auto_scroll = enabled

    def enqueue_text(self, text: str):
        self.command_log.append(f"> {text}")
        if self.auto_scroll:
            cursor = self.command_log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.command_log.setTextCursor(cursor)
            self.command_log.ensureCursorVisible()

    def _send_text_command(self):
        cmd = self.command_input.text().strip()
        if not cmd:
            return
        self.on_command(cmd)
        self.command_log.append(f"< {cmd}")
        if self.auto_scroll:
            cursor = self.command_log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.command_log.setTextCursor(cursor)
            self.command_log.ensureCursorVisible()
        self.command_input.clear()