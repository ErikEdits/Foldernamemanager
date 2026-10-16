"""Regeln für gültige Windows-Datei- und Ordnernamen sowie relative Pfade.

Alle Pfade innerhalb des Programms werden als *relative* Pfade mit ``/`` als
Trennzeichen geführt (z. B. ``"Dokumente/Bericht.pdf"``). Der Wurzelordner ist
``""``. Vergleiche erfolgen wie unter Windows ohne Beachtung der Groß- und
Kleinschreibung.
"""

from __future__ import annotations

import re
import unicodedata

INVALID_CHARS = frozenset('<>:"/\\|?*') | frozenset(chr(i) for i in range(32))

RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
    + ["COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"]
)

MAX_NAME_LENGTH = 255
#: Klassische Grenze für vollständige Pfade unter Windows (ohne Long-Path-Präfix).
MAX_PATH = 260

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PathError(ValueError):
    """Ein Pfad ist ungültig oder nicht erlaubt."""


def name_problems(name: str) -> list[str]:
    """Liefert verständliche Beschreibungen aller Probleme eines Namens (leer = gültig)."""
    problems: list[str] = []
    if not name or not name.strip():
        return ["Der Name ist leer."]
    if name in (".", ".."):
        return [f"„{name}“ ist kein erlaubter Name."]
    bad = sorted({c for c in name if c in INVALID_CHARS})
    if bad:
        shown = " ".join(repr(c)[1:-1] if ord(c) >= 32 else f"\\x{ord(c):02x}" for c in bad)
        problems.append(f"Enthält unter Windows unzulässige Zeichen: {shown}")
    if name != name.rstrip(" ."):
        problems.append("Darf unter Windows nicht mit einem Leerzeichen oder Punkt enden.")
    if name != name.lstrip(" "):
        problems.append("Beginnt mit einem Leerzeichen.")
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if stem in RESERVED_NAMES:
        problems.append(f"„{stem}“ ist unter Windows ein reservierter Name.")
    if len(name) > MAX_NAME_LENGTH:
        problems.append(f"Der Name ist länger als {MAX_NAME_LENGTH} Zeichen.")
    return problems


def is_valid_name(name: str) -> bool:
    return not name_problems(name)


def sanitize_name(name: str, replacement: str = "_") -> str:
    """Macht aus einem beliebigen Text einen gültigen Windows-Namen."""
    cleaned = "".join(replacement if c in INVALID_CHARS else c for c in name)
    cleaned = cleaned.strip().rstrip(" .")
    if not cleaned:
        cleaned = "Unbenannt"
    stem = cleaned.split(".", 1)[0].rstrip(" ").upper()
    if stem in RESERVED_NAMES:
        cleaned = f"{replacement}{cleaned}"
    if len(cleaned) > MAX_NAME_LENGTH:
        root, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) < 16:
            cleaned = root[: MAX_NAME_LENGTH - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            cleaned = cleaned[:MAX_NAME_LENGTH].rstrip(" .")
    return cleaned


def split_rel_path(path: str) -> list[str]:
    """Zerlegt einen relativen Pfad in seine Bestandteile und prüft ihn streng.

    Erlaubt sind nur Pfade *innerhalb* des Wurzelordners: keine Laufwerke,
    keine UNC-Pfade, kein führendes ``/``, keine ``..``-Segmente.
    """
    if path is None:
        raise PathError("Es wurde kein Pfad angegeben.")
    if not isinstance(path, str):
        raise PathError(f"Pfad muss Text sein, erhalten: {type(path).__name__}")
    raw = path.strip()
    if raw in ("", ".", "./"):
        return []
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or norm.startswith("//") or _DRIVE_RE.match(norm):
        raise PathError(f"Absolute Pfade sind nicht erlaubt: „{path}“")
    if norm.startswith("./"):
        norm = norm[2:]
    parts = [p for p in norm.split("/") if p != ""]
    for part in parts:
        if part == "..":
            raise PathError(f"„..“ ist in Pfaden nicht erlaubt: „{path}“")
        if part == ".":
            raise PathError(f"„.“ ist als Pfadbestandteil nicht erlaubt: „{path}“")
    return parts


def normalize_rel_path(path: str) -> str:
    return "/".join(split_rel_path(path))


def path_key(rel_path: str) -> str:
    """Vergleichsschlüssel wie unter Windows (ohne Groß-/Kleinschreibung)."""
    return unicodedata.normalize("NFC", rel_path).casefold()


def join_rel(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def parent_of(rel_path: str) -> str:
    return rel_path.rpartition("/")[0]


def name_of(rel_path: str) -> str:
    return rel_path.rpartition("/")[2]


def is_within(child_key: str, parent_key: str) -> bool:
    """True, wenn ``child_key`` gleich ``parent_key`` ist oder darin liegt (Schlüssel!)."""
    if parent_key == "":
        return True
    return child_key == parent_key or child_key.startswith(parent_key + "/")


def split_ext(name: str) -> tuple[str, str]:
    """Trennt Name und Endung. Punktdateien (".gitignore") haben keine Endung."""
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    if " " in ext or len(ext) > 15:
        return name, ""
    return stem, "." + ext
