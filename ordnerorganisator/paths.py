"""Plattformabhängige Pfade: Desktop, Anwendungsdaten, lange Windows-Pfade."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import APP_ID

IS_WINDOWS = os.name == "nt"


def long_path(path: str | os.PathLike) -> str:
    """Gibt unter Windows einen Pfad mit ``\\\\?\\``-Präfix zurück.

    Damit funktionieren Dateioperationen auch bei Pfaden mit mehr als 260
    Zeichen. Auf anderen Systemen wird der Pfad unverändert zurückgegeben.
    """
    p = os.fspath(path)
    if not IS_WINDOWS:
        return p
    if p.startswith("\\\\?\\"):
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def strip_long_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _windows_known_folder(folder_id: str) -> str | None:
    """Liest einen Windows-Systemordner (z. B. den echten Desktop, auch bei OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        u = uuid.UUID(folder_id)
        guid = GUID()
        guid.Data1, guid.Data2, guid.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            guid.Data4[i] = b
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if hr != 0:
            return None
        value = out.value
        ole32.CoTaskMemFree(out)
        return value
    except Exception:
        return None


FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def desktop_dir() -> Path:
    """Ermittelt den Desktop des Benutzers (berücksichtigt OneDrive-Umleitung)."""
    if IS_WINDOWS:
        found = _windows_known_folder(FOLDERID_DESKTOP)
        if found and os.path.isdir(found):
            return Path(found)
        profile = os.environ.get("USERPROFILE")
        if profile and os.path.isdir(os.path.join(profile, "Desktop")):
            return Path(profile) / "Desktop"
    else:
        try:
            res = subprocess.run(
                ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3
            )
            candidate = res.stdout.strip()
            if res.returncode == 0 and candidate and os.path.isdir(candidate):
                return Path(candidate)
        except (OSError, subprocess.SubprocessError):
            pass
        home_desktop = Path.home() / "Desktop"
        if home_desktop.is_dir():
            return home_desktop
    return Path.home()


def is_cloud_synced(path: str | os.PathLike) -> str | None:
    """Erkennt typische Cloud-Sync-Ordner (z. B. OneDrive) und gibt deren Namen zurück."""
    p = os.path.abspath(os.fspath(path)).casefold()
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env)
        if root and p.startswith(os.path.abspath(root).casefold()):
            return "OneDrive"
    for marker, label in (
        ("onedrive", "OneDrive"),
        ("dropbox", "Dropbox"),
        ("google drive", "Google Drive"),
        ("icloud", "iCloud"),
    ):
        if marker in p:
            return label
    return None


def app_data_dir() -> Path:
    """Ordner für Einstellungen (Roaming) – enthält niemals API-Keys im Klartext."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.join(Path.home(), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(Path.home(), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    override = os.environ.get("KI_ORDNERORGANISATOR_HOME")
    path = Path(override) if override else Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    """Ordner für Vorgänge (Journal, Protokolle) – ermöglicht Fortsetzen und Rückgängig."""
    if IS_WINDOWS and not os.environ.get("KI_ORDNERORGANISATOR_HOME"):
        base = os.environ.get("LOCALAPPDATA")
        path = Path(base) / APP_ID / "runs" if base else app_data_dir() / "runs"
    else:
        path = app_data_dir() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_in_file_manager(path: str | os.PathLike) -> None:
    p = os.fspath(path)
    if IS_WINDOWS:
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])
