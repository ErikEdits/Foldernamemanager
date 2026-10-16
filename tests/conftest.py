from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path_factory, monkeypatch):
    """Einstellungen und Vorgänge der Tests landen nie im echten Benutzerprofil."""
    home = tmp_path_factory.mktemp("apphome")
    monkeypatch.setenv("KI_ORDNERORGANISATOR_HOME", str(home))
    yield home


def make_tree(root: Path, spec: dict) -> Path:
    """Legt eine Ordnerstruktur an. Dict = Ordner, str/bytes = Dateiinhalt."""
    root.mkdir(parents=True, exist_ok=True)
    for name, value in spec.items():
        p = root / name
        if isinstance(value, dict):
            make_tree(p, value)
        elif isinstance(value, bytes):
            p.write_bytes(value)
        else:
            p.write_text(value, encoding="utf-8")
    return root


def snapshot_files(root: Path) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = Path(dirpath) / f
            out[full.relative_to(root).as_posix()] = full.read_bytes()
    return out


def snapshot_dirs(root: Path) -> set[str]:
    out = set()
    for dirpath, dirs, _files in os.walk(root):
        for d in dirs:
            out.add((Path(dirpath) / d).relative_to(root).as_posix())
    return out


@pytest.fixture
def example_project(tmp_path) -> Path:
    """Das Beispiel aus der Spezifikation (Abschnitt 5)."""
    return make_tree(
        tmp_path / "MeinProjekt",
        {
            "Downloads neu": {"setup.exe": b"MZ" + b"\0" * 100, "notiz.txt": "hallo"},
            "datei1.txt": "Projektnotizen\nErste Idee",
            "Bilder": {"urlaub.jpg": b"\xff\xd8\xff" + b"1" * 50},
            "final_final_v2.pdf": b"%PDF-1.4 test",
        },
    )
