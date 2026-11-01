"""Setting rows with an aligned, local reset action before the editor."""
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

EDIT_COLOR = "#ed7a27"
OMITTED_COLOR = "#999999"


def _reset_icon():
    # Draw a vector arrow so its appearance does not depend on installed fonts.
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{EDIT_COLOR}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
           '<path d="M3 11a9 9 0 1 1 2.8 6.5"/><path d="M3 4v7h7"/></svg>')
    renderer = QSvgRenderer(svg.encode())
    icon = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(16 * scale, 16 * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, 16, 16))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _reset_button(text, reset_callback):
    reset = QToolButton()
    reset.setIcon(_reset_icon())
    reset.setIconSize(QSize(16, 16))
    reset.setAccessibleName(f"Reset {text}")
    reset.setToolTip("Reset to the last read or loaded value")
    reset.setAutoRaise(True)
    reset.setCursor(Qt.CursorShape.PointingHandCursor)
    reset.setFixedSize(22, 22)
    reset.setStyleSheet(f"QToolButton {{ color: {EDIT_COLOR}; border: none; padding: 0; }}"
                       "QToolButton:hover { background: rgba(237, 122, 39, 30); border-radius: 3px; }")
    policy = reset.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    reset.setSizePolicy(policy)
    reset.clicked.connect(reset_callback)
    reset.hide()
    return reset


def setting_row(text, editor, reset_callback):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(text)
    reset = _reset_button(text, reset_callback)
    layout.addWidget(reset)
    layout.addWidget(editor, 1)
    return label, row, reset


def setting_heading(text, reset_callback):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(text)
    reset = _reset_button(text, reset_callback)
    layout.addWidget(label)
    layout.addWidget(reset)
    layout.addStretch()
    return row, label, reset


def update_setting_label(label, reset, modified, enabled, *, omitted=False, resettable=None):
    label.setProperty("omitted", omitted)
    label.setStyleSheet(f"color: {OMITTED_COLOR};" if omitted else f"color: {EDIT_COLOR};" if modified else "")
    show_reset = modified if resettable is None else resettable
    reset.setVisible(show_reset)
    reset.setEnabled(show_reset and enabled)
