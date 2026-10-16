"""Datenmodell für die erfasste Ordnerstruktur (Snapshot)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .windows_names import name_of, parent_of, path_key

#: Ordner, deren Inhalt die integrierte KI niemals umorganisiert und die in
#: Analysepaketen nur zusammengefasst werden.
PROTECTED_DIR_NAMES = frozenset(
    n.casefold()
    for n in (
        ".git", ".svn", ".hg", ".idea", ".vscode", ".vs", ".gradle", ".mvn",
        "node_modules", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
        ".pytest_cache", "$RECYCLE.BIN", "System Volume Information", ".Trash",
        ".cache", ".settings", "bower_components", ".next", ".nuxt", "dist-newstyle",
    )
)

#: Dateien, die nie umbenannt oder verschoben werden (Systemdateien, Projektdateien).
PROTECTED_FILE_NAMES = frozenset(
    n.casefold()
    for n in (
        "desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db", "folder.jpg",
        "albumart.jpg", ".gitignore", ".gitattributes", ".gitmodules", ".editorconfig",
    )
)

#: Dateinamen (ohne Endung, kleingeschrieben), die konventionell im Wurzelordner liegen.
CONVENTIONAL_ROOT_STEMS = frozenset(
    ["readme", "license", "licence", "lizenz", "changelog", "contributing", "authors",
     "notice", "copying", "code_of_conduct", "security"]
)


@dataclass
class Entry:
    rel_path: str
    name: str
    is_dir: bool
    size: int = 0
    ext: str = ""
    category: str = ""
    hidden: bool = False
    system: bool = False
    is_link: bool = False
    readonly: bool = False
    mtime: float = 0.0
    error: str | None = None
    children: list["Entry"] = field(default_factory=list)
    #: Anzahl aller Dateien unterhalb (nur Ordner)
    file_count: int = 0
    #: Gesamtgröße unterhalb (nur Ordner)
    total_size: int = 0

    @property
    def key(self) -> str:
        return path_key(self.rel_path)

    @property
    def parent_rel(self) -> str:
        return parent_of(self.rel_path)

    @property
    def depth(self) -> int:
        return 0 if not self.rel_path else self.rel_path.count("/") + 1

    @property
    def is_special(self) -> bool:
        """Versteckte/System-Dateien, Office-Sperrdateien und bekannte Systemdateien."""
        low = self.name.casefold()
        return (
            self.hidden
            or self.system
            or low in PROTECTED_FILE_NAMES
            or low.startswith("~$")
            or (low.startswith(".") and len(low) > 1)
        )

    def iter_tree(self):
        """Alle Einträge unterhalb (Tiefensuche, Eltern vor Kindern), ohne sich selbst."""
        stack = list(reversed(self.children))
        while stack:
            e = stack.pop()
            yield e
            if e.children:
                stack.extend(reversed(e.children))


@dataclass
class Snapshot:
    root_path: str
    root: Entry
    entries: dict[str, Entry]  # Schlüssel: path_key(rel_path)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    scanned_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def root_name(self) -> str:
        return self.root.name

    def get(self, rel_path: str) -> Entry | None:
        return self.entries.get(path_key(rel_path))

    def files(self) -> list[Entry]:
        return [e for e in self.root.iter_tree() if not e.is_dir]

    def dirs(self) -> list[Entry]:
        return [e for e in self.root.iter_tree() if e.is_dir]

    @property
    def file_count(self) -> int:
        return self.root.file_count

    @property
    def dir_count(self) -> int:
        return sum(1 for e in self.root.iter_tree() if e.is_dir)

    @property
    def total_size(self) -> int:
        return self.root.total_size

    @property
    def link_count(self) -> int:
        return sum(1 for e in self.root.iter_tree() if e.is_link)

    def is_protected(self, rel_path: str) -> bool:
        """True, wenn der Pfad in einem geschützten Ordner liegt (z. B. .git, node_modules)."""
        parts = rel_path.split("/") if rel_path else []
        return any(p.casefold() in PROTECTED_DIR_NAMES for p in parts)

    def parent_entry(self, entry: Entry) -> Entry:
        return self.entries[path_key(entry.parent_rel)]


def format_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{num} B"


def leaf(rel_path: str) -> str:
    return name_of(rel_path)
