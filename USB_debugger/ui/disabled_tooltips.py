"""Show existing tooltips even when Qt routes hover help to a disabled control's parent."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QToolTip, QWidget


class DisabledTooltips(QObject):
    """Resolve the control under a tooltip event instead of relying on enabled hit testing."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.ToolTip or not isinstance(event, QHelpEvent):
            return False
        control = QApplication.widgetAt(event.globalPos())
        if control is None and isinstance(watched, QWidget):
            control = watched
        if control is None or not self.window.isAncestorOf(control) or control.isEnabled():
            return False
        while control is not None and control is not self.window:
            if control.toolTip():
                QToolTip.showText(event.globalPos(), control.toolTip(), self.window)
                return True
            control = control.parentWidget()
        return False
