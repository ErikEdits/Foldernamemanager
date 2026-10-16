"""Basisklasse der Assistenten-Seiten."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .widgets import heading, muted

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


class Page(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.session = win.session
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(18, 14, 18, 10)
        self.layout_.setSpacing(10)
        self.layout_.addWidget(heading(self.title, 16))
        if self.subtitle:
            self.layout_.addWidget(muted(self.subtitle))

    # Navigation -----------------------------------------------------------
    def enter(self) -> None:
        """Wird beim Anzeigen der Seite aufgerufen."""

    def next_label(self) -> str:
        return "Weiter"

    def next_enabled(self) -> bool:
        return True

    def back_enabled(self) -> bool:
        return True

    def busy(self) -> bool:
        return False

    def on_next(self) -> None:
        self.win.go_next()
