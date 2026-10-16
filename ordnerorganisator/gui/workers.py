"""Hintergrund-Aufgaben, damit die Oberfläche bei langen Vorgängen bedienbar bleibt."""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..ai.base import AnalysisCancelled, ProviderError
from ..executor import ExecutionCancelled, PreparationError, SafetyError
from ..plan import PlanFormatError
from ..redaction import redact
from ..scanner import ScanCancelled, ScanError, explain_os_error

CANCEL_TYPES = (ScanCancelled, AnalysisCancelled, ExecutionCancelled)
KNOWN_ERRORS = (ScanError, ProviderError, PlanFormatError, PreparationError, SafetyError)


class Worker(QThread):
    """Führt ``fn(progress, cancel_event)`` in einem eigenen Thread aus."""

    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[[Callable[[Any], None], threading.Event], Any], parent=None,
                 min_interval: float = 0.05):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()
        self._min_interval = min_interval
        self._last = 0.0

    def _emit_progress(self, value: Any) -> None:
        now = time.monotonic()
        if now - self._last >= self._min_interval:
            self._last = now
            self.progress.emit(value)

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:  # noqa: D401 - Qt-API
        try:
            result = self._fn(self._emit_progress, self.cancel_event)
        except CANCEL_TYPES:
            self.cancelled.emit()
        except KNOWN_ERRORS as exc:
            self.failed.emit(redact(str(exc)))
        except OSError as exc:
            self.failed.emit(redact(explain_os_error(exc)))
        except Exception as exc:  # pragma: no cover - Schutznetz
            traceback.print_exc()
            self.failed.emit(redact(f"Unerwarteter Fehler: {type(exc).__name__}: {exc}"))
        else:
            self.succeeded.emit(result)
