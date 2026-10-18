"""Command buttons with scoped click ownership and reply-driven busy state."""
from __future__ import annotations

from typing import ClassVar
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton


class BusyButton(QPushButton):
    """Keep a button busy until every command submitted by its click finishes.

    Command handlers connect to ``activated`` instead of ``clicked`` so their
    originating button is known only during synchronous click handling. Timer
    polls and later replies cannot accidentally inherit that ownership.
    """

    activated = Signal(bool)
    _current: ClassVar[BusyButton | None] = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending_count = 0
        self._generation = 0
        self._normal_text = super().text()
        self._allowed = self.isEnabled()
        self._normal_cursor = self.cursor()
        self.clicked.connect(self._activate)

    @classmethod
    def current(cls) -> BusyButton | None:
        return cls._current

    def _activate(self, checked=False):
        previous = BusyButton._current
        BusyButton._current = self
        try:
            self.activated.emit(checked)
        finally:
            BusyButton._current = previous

    @property
    def is_busy(self):
        return self._pending_count > 0

    def begin_busy(self) -> int:
        if not self.is_busy:
            self._generation += 1
            self._normal_cursor = self.cursor()
        self._pending_count += 1
        self._update_busy()
        return self._generation

    def end_busy(self, generation: int | None = None):
        if generation is not None and generation != self._generation:
            return
        self._pending_count = max(0, self._pending_count - 1)
        self._update_busy()

    def reset_busy(self):
        self._generation += 1
        self._pending_count = 0
        self._update_busy()

    def setEnabled(self, enabled):
        self._allowed = bool(enabled)
        super().setEnabled(self._allowed and not self.is_busy)

    def setDisabled(self, disabled):
        self.setEnabled(not disabled)

    def setText(self, text):
        self._normal_text = text
        self._update_busy()

    def _update_busy(self):
        super().setText(f"{self._normal_text} — Working…" if self.is_busy else self._normal_text)
        super().setEnabled(self._allowed and not self.is_busy)
        self.setCursor(Qt.CursorShape.WaitCursor if self.is_busy else self._normal_cursor)
