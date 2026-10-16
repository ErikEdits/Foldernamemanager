"""Sichere Ablage von API-Keys.

Reihenfolge der Speicherorte:

1. **Windows-Anmeldeinformationsverwaltung** (über das Paket ``keyring``),
2. **Windows DPAPI** – verschlüsselt an das Benutzerkonto gebunden,
3. nur für die laufende Sitzung im Arbeitsspeicher (nichts wird gespeichert).

Ein API-Key wird niemals im Klartext auf die Festplatte geschrieben und
niemals in Einstellungen, Protokolle oder Analysepakete übernommen.
"""

from __future__ import annotations

import base64
import json
import os

from . import APP_ID
from .paths import IS_WINDOWS, app_data_dir
from .redaction import REDACTOR


class _MemoryBackend:
    name = "nur für diese Sitzung (wird nicht gespeichert)"
    persistent = False

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _KeyringBackend:
    name = "Windows-Anmeldeinformationsverwaltung"
    persistent = True

    def __init__(self) -> None:
        import keyring  # noqa: F401
        from keyring.backends import fail

        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or "null" in type(kr).__name__.lower():
            raise RuntimeError("Kein sicherer Schlüsselspeicher verfügbar")
        if not IS_WINDOWS:
            self.name = f"Schlüsselbund des Systems ({type(kr).__name__})"
        self._kr = keyring

    def get(self, key: str) -> str | None:
        return self._kr.get_password(APP_ID, key)

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(APP_ID, key, value)

    def delete(self, key: str) -> None:
        try:
            self._kr.delete_password(APP_ID, key)
        except Exception:
            pass


class _DpapiBackend:
    """Verschlüsselt mit der Windows Data Protection API (nur dieses Benutzerkonto)."""

    name = "Windows DPAPI (verschlüsselt, an Ihr Benutzerkonto gebunden)"
    persistent = True

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("DPAPI ist nur unter Windows verfügbar")
        self._file = app_data_dir() / "secrets.dpapi.json"

    @staticmethod
    def _crypt(data: bytes, protect: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = BLOB()
        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
        if not ok:
            raise OSError("DPAPI-Vorgang fehlgeschlagen")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)  # type: ignore[attr-defined]

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._file)

    def get(self, key: str) -> str | None:
        enc = self._load().get(key)
        if not enc:
            return None
        return self._crypt(base64.b64decode(enc), protect=False).decode("utf-8")

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = base64.b64encode(self._crypt(value.encode("utf-8"), protect=True)).decode("ascii")
        self._save(data)

    def delete(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)


class SecretStore:
    def __init__(self, backend=None) -> None:
        self._session = _MemoryBackend()
        if backend is not None:
            self.backend = backend
            return
        self.backend = None
        for cls in (_KeyringBackend, _DpapiBackend):
            try:
                self.backend = cls()
                break
            except Exception:
                continue
        if self.backend is None:
            self.backend = self._session

    @property
    def backend_name(self) -> str:
        return self.backend.name

    @property
    def can_persist(self) -> bool:
        return bool(getattr(self.backend, "persistent", False))

    def get(self, key: str) -> str | None:
        value = self._session.get(key)
        if value is None and self.backend is not self._session:
            try:
                value = self.backend.get(key)
            except Exception:
                value = None
        REDACTOR.register(value)
        return value

    def set(self, key: str, value: str, persist: bool = True) -> None:
        value = value.strip()
        REDACTOR.register(value)
        self._session.set(key, value)
        if persist and self.can_persist:
            self.backend.set(key, value)

    def delete(self, key: str) -> None:
        REDACTOR.forget(self._session.get(key))
        self._session.delete(key)
        if self.backend is not self._session:
            try:
                self.backend.delete(key)
            except Exception:
                pass

    def has(self, key: str) -> bool:
        return bool(self.get(key))


def api_key_name(provider_kind: str) -> str:
    return f"api_key:{provider_kind}"
