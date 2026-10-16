"""Erfasst die Struktur eines Ordners – rein lesend, ohne irgendetwas zu verändern."""

from __future__ import annotations

import os
import stat
import threading
from typing import Callable

from .filetypes import category_for
from .models import Entry, Snapshot
from .paths import long_path
from .windows_names import path_key, split_ext

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

ProgressFn = Callable[[int, str], None]


class ScanCancelled(Exception):
    pass


class ScanError(Exception):
    pass


def _is_link(dirent: os.DirEntry) -> bool:
    try:
        if dirent.is_symlink():
            return True
        is_junction = getattr(dirent, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        st = dirent.stat(follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) and dirent.is_dir(follow_symlinks=False)
    except OSError:
        return False


def explain_os_error(exc: BaseException) -> str:
    """Übersetzt Betriebssystemfehler in verständliche deutsche Meldungen."""
    winerror = getattr(exc, "winerror", None)
    if winerror in (32, 33):
        return "Die Datei ist gesperrt (wird von einem anderen Programm verwendet)."
    if winerror in (112, 39) or getattr(exc, "errno", None) == 28:
        return "Nicht genügend Speicherplatz auf dem Ziellaufwerk."
    if winerror in (206,) or getattr(exc, "errno", None) == 36:
        return "Der Pfad oder Dateiname ist zu lang."
    if winerror == 1920:
        return "Auf die Datei kann vom System nicht zugegriffen werden."
    if isinstance(exc, PermissionError):
        return "Zugriff verweigert – die Berechtigung fehlt oder die Datei ist schreibgeschützt."
    if isinstance(exc, FileNotFoundError):
        return "Die Datei oder der Ordner wurde nicht gefunden."
    if isinstance(exc, FileExistsError):
        return "Ein Element mit diesem Namen ist bereits vorhanden."
    if isinstance(exc, IsADirectoryError):
        return "Es wurde eine Datei erwartet, gefunden wurde ein Ordner."
    if isinstance(exc, NotADirectoryError):
        return "Es wurde ein Ordner erwartet, gefunden wurde eine Datei."
    msg = getattr(exc, "strerror", None) or str(exc)
    return f"Unerwarteter Fehler: {msg}"


def scan_folder(
    root_path: str | os.PathLike,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Snapshot:
    """Liest die komplette Struktur des Ordners ``root_path`` ein.

    * Symbolische Links und Verzeichnisverbindungen (Junctions) werden nicht
      verfolgt, sondern als Verknüpfung erfasst.
    * Ordner ohne Leserecht werden mit Fehlerhinweis erfasst, der Scan läuft weiter.
    """
    root_abs = os.path.abspath(os.fspath(root_path))
    if not os.path.exists(long_path(root_abs)):
        raise ScanError(f"Der Ordner „{root_abs}“ existiert nicht.")
    if not os.path.isdir(long_path(root_abs)):
        raise ScanError(f"„{root_abs}“ ist kein Ordner.")

    root_name = os.path.basename(root_abs.rstrip("\\/")) or root_abs
    root = Entry(rel_path="", name=root_name, is_dir=True)
    entries: dict[str, Entry] = {"": root}
    skipped: list[tuple[str, str]] = []
    counter = 0

    stack: list[tuple[str, Entry]] = [(root_abs, root)]
    while stack:
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        abs_dir, dir_entry = stack.pop()
        try:
            with os.scandir(long_path(abs_dir)) as it:
                dirents = sorted(it, key=lambda d: (d.name.casefold(), d.name))
        except OSError as exc:
            dir_entry.error = explain_os_error(exc)
            skipped.append((dir_entry.rel_path or ".", dir_entry.error))
            continue

        sub_dirs: list[tuple[str, Entry]] = []
        for d in dirents:
            rel = f"{dir_entry.rel_path}/{d.name}" if dir_entry.rel_path else d.name
            link = _is_link(d)
            try:
                st = d.stat(follow_symlinks=False)
            except OSError as exc:
                skipped.append((rel, explain_os_error(exc)))
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            is_dir = (not link) and d.is_dir(follow_symlinks=False)
            hidden = bool(attrs & FILE_ATTRIBUTE_HIDDEN)
            if not attrs and d.name.startswith("."):
                hidden = True
            entry = Entry(
                rel_path=rel,
                name=d.name,
                is_dir=is_dir,
                hidden=hidden,
                system=bool(attrs & FILE_ATTRIBUTE_SYSTEM),
                readonly=bool(attrs & FILE_ATTRIBUTE_READONLY) or not (st.st_mode & stat.S_IWUSR),
                is_link=link,
                mtime=st.st_mtime,
            )
            if not is_dir:
                if not link:
                    entry.size = st.st_size
                entry.ext = split_ext(d.name)[1].lower()
                entry.category = category_for(entry.ext).key
            dir_entry.children.append(entry)
            entries[path_key(rel)] = entry
            if link:
                skipped.append((rel, "Verknüpfung (Symlink/Junction) – wird nicht verfolgt und nicht kopiert."))
            elif is_dir:
                sub_dirs.append((os.path.join(abs_dir, d.name), entry))
            counter += 1
            if progress is not None and counter % 200 == 0:
                progress(counter, rel)
        # Unterordner in alphabetischer Reihenfolge abarbeiten
        stack.extend(reversed(sub_dirs))

    _compute_totals(root)
    if progress is not None:
        progress(counter, "")
    return Snapshot(root_path=root_abs, root=root, entries=entries, skipped=skipped)


def _compute_totals(root: Entry) -> None:
    order = [root] + [e for e in root.iter_tree() if e.is_dir]
    for d in reversed(order):
        files = 0
        size = 0
        for c in d.children:
            if c.is_dir:
                files += c.file_count
                size += c.total_size
            elif not c.is_link:
                files += 1
                size += c.size
        d.file_count = files
        d.total_size = size
