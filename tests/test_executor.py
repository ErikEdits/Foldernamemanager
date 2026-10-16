from __future__ import annotations

import shutil
import threading
from collections import namedtuple
from pathlib import Path

import pytest

from ordnerorganisator import executor as ex
from ordnerorganisator.executor import (
    ExecutionCancelled,
    Executor,
    PathGuard,
    RunState,
    SafetyError,
    create_run,
    list_unfinished_runs,
    prepare_run,
)
from ordnerorganisator.plan import CREATE_FOLDER, MOVE, RENAME, Operation, Plan
from ordnerorganisator.planner import resolve_plan
from ordnerorganisator.scanner import scan_folder

from conftest import make_tree, snapshot_dirs, snapshot_files


def _run(src: Path, dest_parent: Path, ops: list[Operation]):
    snap = scan_folder(src)
    plan = Plan(operations=ops)
    res = resolve_plan(snap, plan)
    assert not res.has_errors, res.issues
    prep = prepare_run(snap, res, dest_parent=str(dest_parent))
    assert prep.ok, prep.errors
    state = create_run(prep, snap, plan, res)
    return snap, plan, res, state


SPEC_OPS = [
    Operation("op1", CREATE_FOLDER, target="Dokumente"),
    Operation("op2", MOVE, source="datei1.txt", target="Dokumente/datei1.txt"),
    Operation("op3", RENAME, source="Downloads neu", target="Downloads_Neu"),
    Operation("op4", MOVE, source="final_final_v2.pdf", target="Dokumente/Final_v2.pdf"),
    Operation("op5", CREATE_FOLDER, target="Leer/Unterordner"),
]


def test_full_run_keeps_original_untouched(example_project, tmp_path):
    before_files = snapshot_files(example_project)
    before_dirs = snapshot_dirs(example_project)
    desk = tmp_path / "Desktop"
    desk.mkdir()
    _snap, _plan, _res, state = _run(example_project, desk, [Operation(**o.__dict__) for o in SPEC_OPS])
    Executor(state).run_all()

    # Original unverändert
    assert snapshot_files(example_project) == before_files
    assert snapshot_dirs(example_project) == before_dirs

    dest = Path(state.dest_root)
    assert dest.name == "MeinProjekt_Organisiert"
    assert dest.parent == desk
    files = snapshot_files(dest)
    assert set(files) == {
        "Dokumente/datei1.txt",
        "Dokumente/Final_v2.pdf",
        "Downloads_Neu/setup.exe",
        "Downloads_Neu/notiz.txt",
        "Bilder/urlaub.jpg",
    }
    assert files["Dokumente/Final_v2.pdf"] == before_files["final_final_v2.pdf"]
    dirs = snapshot_dirs(dest)
    assert "Downloads neu" not in dirs  # leer gewordener Ordner wurde entfernt
    assert "Leer/Unterordner" in dirs
    assert ".kiorg-staging" not in dirs
    assert state.phase == ex.PH_DONE
    assert state.report_file.exists()
    report = state.report_file.read_text(encoding="utf-8")
    assert "ausgeführt" in report and "op4" in report

    # Rückgängig: Kopie hat wieder die Originalstruktur
    Executor(RunState.load(state.run_id)).undo()
    assert snapshot_files(dest) == before_files
    assert snapshot_dirs(dest) == before_dirs
    assert snapshot_files(example_project) == before_files


def test_second_run_gets_unique_name(example_project, tmp_path):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "MeinProjekt_Organisiert").mkdir()
    _s, _p, _r, state = _run(example_project, desk, [])
    assert Path(state.dest_root).name == "MeinProjekt_Organisiert_2"


def test_swap_and_new_file_after_scan(tmp_path):
    src = make_tree(tmp_path / "R", {"a.txt": "A", "b.txt": "B", "alt": {"x.txt": "X"}})
    desk = tmp_path / "D"
    desk.mkdir()
    _s, _p, _r, state = _run(
        src,
        desk,
        [
            Operation("op1", RENAME, source="a.txt", target="b.txt"),
            Operation("op2", RENAME, source="b.txt", target="a.txt"),
            Operation("op3", RENAME, source="alt", target="neu"),
        ],
    )
    (src / "alt" / "spaeter.txt").write_text("S")  # nach der Analyse hinzugekommen
    Executor(state).run_all()
    files = snapshot_files(Path(state.dest_root))
    assert files == {"a.txt": b"B", "b.txt": b"A", "neu/x.txt": b"X", "neu/spaeter.txt": b"S"}


def test_cancel_and_resume(tmp_path):
    src = make_tree(tmp_path / "R", {f"f{i}.txt": "x" * 10 for i in range(30)})
    desk = tmp_path / "D"
    desk.mkdir()
    ops = [Operation(f"op{i}", MOVE, source=f"f{i}.txt", target=f"Ordner/f{i}.txt") for i in range(30)]
    _s, _p, _r, state = _run(src, desk, ops)

    cancel = threading.Event()
    seen = []

    def progress(p):
        seen.append(p)
        if p.phase == "copy" and len(seen) > 10:
            cancel.set()

    with pytest.raises(ExecutionCancelled):
        Executor(state, progress=progress, cancel=cancel).run_all()
    assert [r.run_id for r in list_unfinished_runs()] == [state.run_id]

    resumed = RunState.load(state.run_id)
    assert resumed.phase == ex.PH_COPYING
    Executor(resumed).run_all()
    files = snapshot_files(Path(state.dest_root))
    assert set(files) == {f"Ordner/f{i}.txt" for i in range(30)}
    assert not list_unfinished_runs()


def test_resume_during_apply_reconciles(tmp_path):
    src = make_tree(tmp_path / "R", {"a.txt": "A", "b.txt": "B", "c.txt": "C"})
    desk = tmp_path / "D"
    desk.mkdir()
    _s, _p, _r, state = _run(
        src, desk,
        [Operation("op1", MOVE, source=n, target=f"Neu/{n}") for n in ("a.txt", "b.txt", "c.txt")],
    )
    exe = Executor(state)
    exe.copy_phase()
    # Simulierter Absturz: Umsetzen begonnen, eine Datei schon im Zwischenordner,
    # das Journal wurde aber nicht mehr gespeichert.
    state.phase = ex.PH_COPIED
    cancel = threading.Event()
    calls = {"n": 0}
    real_rename = ex.os.rename

    def flaky_rename(a, b):
        calls["n"] += 1
        real_rename(a, b)
        if calls["n"] == 2:
            cancel.set()

    exe2 = Executor(state, cancel=cancel)
    ex.os.rename = flaky_rename
    try:
        with pytest.raises(ExecutionCancelled):
            exe2.apply_phase()
    finally:
        ex.os.rename = real_rename
    # Journalstand absichtlich "veralten" lassen
    for m in state.moves:
        m["status"] = "pending"
    state.save()
    Executor(RunState.load(state.run_id)).run_all()
    assert snapshot_files(Path(state.dest_root)) == {"Neu/a.txt": b"A", "Neu/b.txt": b"B", "Neu/c.txt": b"C"}


def test_guard_blocks_outside_writes(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    guard = PathGuard(str(dest), str(src))
    guard.check(str(dest / "a" / "b.txt"))
    with pytest.raises(SafetyError):
        guard.check(str(src / "x.txt"))
    with pytest.raises(SafetyError):
        guard.check(str(tmp_path / "anderswo.txt"))
    with pytest.raises(SafetyError):
        PathGuard(str(src / "kopie"), str(src))


def test_prepare_detects_overlap_and_space(example_project, tmp_path, monkeypatch):
    snap = scan_folder(example_project)
    res = resolve_plan(snap, Plan())
    prep = prepare_run(snap, res, dest_parent=str(example_project))
    assert not prep.ok and "innerhalb des Originals" in prep.errors[0]

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(ex.shutil, "disk_usage", lambda p: Usage(100, 100, 10))
    desk = tmp_path / "D"
    desk.mkdir()
    prep = prepare_run(snap, res, dest_parent=str(desk))
    assert any("Speicherplatz" in e for e in prep.errors)


def test_create_run_refuses_errors(example_project, tmp_path):
    snap = scan_folder(example_project)
    plan = Plan(operations=[Operation("op1", MOVE, source="gibtsnicht", target="x")])
    res = resolve_plan(snap, plan)
    desk = tmp_path / "D"
    desk.mkdir()
    prep = prepare_run(snap, res, dest_parent=str(desk))
    assert not prep.ok
    with pytest.raises(ex.PreparationError):
        create_run(prep, snap, plan, res)


def test_copy_error_is_reported_and_continues(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "R", {"ok.txt": "1", "gesperrt.txt": "2"})
    desk = tmp_path / "D"
    desk.mkdir()
    _s, _p, _r, state = _run(src, desk, [])
    real_open = open

    def fake_open(path, mode="r", *a, **kw):
        if str(path).endswith("gesperrt.txt") and "r" in mode:
            err = PermissionError(13, "Permission denied")
            raise err
        return real_open(path, mode, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr("builtins.open", fake_open)
        errors = Executor(state).copy_phase()
    assert [e["pfad"] for e in errors] == ["gesperrt.txt"]
    assert "Zugriff verweigert" in errors[0]["fehler"]
    # erneuter Versuch kopiert nur noch die fehlende Datei
    errors = Executor(RunState.load(state.run_id)).retry_copy()
    assert errors == []
    assert set(snapshot_files(Path(state.dest_root))) == {"ok.txt", "gesperrt.txt"}


def test_symlinks_are_not_followed(tmp_path):
    src = make_tree(tmp_path / "R", {"a.txt": "1"})
    outside = make_tree(tmp_path / "outside", {"geheim.txt": "x"})
    try:
        (src / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks nicht verfügbar")
    snap = scan_folder(src)
    assert snap.get("link").is_link
    assert snap.get("link/geheim.txt") is None
    desk = tmp_path / "D"
    desk.mkdir()
    _s, _p, _r, state = _run(src, desk, [])
    Executor(state).run_all()
    assert set(snapshot_files(Path(state.dest_root))) == {"a.txt"}
    shutil.rmtree(outside)
