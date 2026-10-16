"""Verhindert, dass Geheimnisse (API-Keys, Tokens) in Pakete, Protokolle oder Exporte gelangen."""

from __future__ import annotations

import re
import threading

_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
]

REPLACEMENT = "[ENTFERNT]"


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str | None) -> None:
        if secret and len(secret) >= 6:
            with self._lock:
                self._secrets.add(secret)

    def forget(self, secret: str | None) -> None:
        with self._lock:
            self._secrets.discard(secret or "")

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for s in secrets:
            text = text.replace(s, REPLACEMENT)
        for rx in _PATTERNS:
            text = rx.sub(REPLACEMENT, text)
        return text

    def contains_secret(self, text: str) -> bool:
        return self.redact(text) != text


#: Prozessweiter Filter – jeder geladene API-Key wird hier registriert.
REDACTOR = Redactor()


def redact(text: str) -> str:
    return REDACTOR.redact(text)
