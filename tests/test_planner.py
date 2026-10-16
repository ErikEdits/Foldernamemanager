from __future__ import annotations

from ordnerorganisator.plan import CREATE_FOLDER, MOVE, RENAME, Operation, Plan, plan_from_json
from ordnerorganisator.planner import (
    ERROR,
    MERGED,
    MOVED,
    NEW,
    RENAMED,
    WARNING,
    auto_reject_invalid,
    resolve_plan,
)
from ordnerorganisator.scanner import scan_folder

from conftest import make_tree


def _plan(*ops: Operation) -> Plan:
    return Plan(operations=list(ops))


def test_scan_example(example_project):
    snap = scan_folder(example_project)
    assert snap.root_name == "MeinProjekt"
    assert snap.file_count == 5
    assert snap.dir_count == 2
    assert snap.get("downloads NEU/Setup.EXE") is not None  # wie Windows: ohne Groß-/Kleinschreibung
    assert snap.get("Bilder/urlaub.jpg").category == "images"


def test_resolve_basic_moves(example_project):
    snap = scan_folder(example_project)
    plan = _plan(
        Operation("op1", CREATE_FOLDER, target="Dokumente"),
        Operation("op2", MOVE, source="datei1.txt", target="Dokumente/datei1.txt"),
        Operation("op3", RENAME, source="Downloads neu", target="Downloads_Neu"),
        Operation("op4", MOVE, source="final_final_v2.pdf", target="Dokumente/Final_v2.pdf"),
    )
    res = resolve_plan(snap, plan)
    assert not res.has_errors, res.issues
    assert res.file_map["datei1.txt"] == "Dokumente/datei1.txt"
    assert res.file_map["downloads neu/setup.exe"] == "Downloads_Neu/setup.exe"
    assert res.dir_map["downloads neu"] == "Downloads_Neu"
    statuses = {c.name: c.status for c in res.root.children}
    assert statuses["Dokumente"] == NEW
    assert statuses["Downloads_Neu"] == RENAMED
    docs = next(c for c in res.root.children if c.name == "Dokumente")
    assert {c.name: c.status for c in docs.children} == {"datei1.txt": MOVED, "Final_v2.pdf": "moved_renamed"}


def test_duplicate_targets_are_errors(example_project):
    snap = scan_folder(example_project)
    plan = _plan(
        Operation("op1", MOVE, source="datei1.txt", target="X/a.txt"),
        Operation("op2", MOVE, source="Downloads neu/notiz.txt", target="x/A.TXT"),
    )
    res = resolve_plan(snap, plan)
    errors = res.errors
    assert errors and errors[0].op_id == "op2"
    res2, rejected = auto_reject_invalid(snap, plan)
    assert [op.id for op in rejected] == ["op2"]
    assert not res2.has_errors


def test_invalid_names_and_paths(example_project):
    snap = scan_folder(example_project)
    plan = plan_from_json(
        """{"operations": [
            {"type": "move", "source": "datei1.txt", "target": "../outside.txt"},
            {"type": "move", "source": "datei1.txt", "target": "C:/Windows/evil.txt"},
            {"type": "rename", "source": "Bilder", "target": "CON"},
            {"type": "rename", "source": "Bilder", "target": "Bild:er"},
            {"type": "delete", "source": "datei1.txt"},
            {"type": "move", "source": "gibtsnicht.txt", "target": "a.txt"},
            {"type": "move", "source": "Bilder", "target": "Bilder/Unter/Bilder"}
        ]}"""
    )
    res = resolve_plan(snap, plan)
    by_op = {}
    for issue in res.issues:
        by_op.setdefault(issue.op_id, []).append(issue)
    for i in range(1, 8):
        assert any(x.level == ERROR for x in by_op.get(f"op{i}", [])), f"op{i} sollte abgelehnt werden"
    res2, rejected = auto_reject_invalid(snap, plan)
    assert len(rejected) == 7
    assert not res2.has_errors


def test_file_folder_conflict_and_inside_file(tmp_path):
    root = make_tree(tmp_path / "R", {"a.txt": "1", "b": {"c.txt": "2"}})
    snap = scan_folder(root)
    res = resolve_plan(snap, _plan(Operation("op1", RENAME, source="b", target="a.txt")))
    assert res.has_errors
    res = resolve_plan(snap, _plan(Operation("op1", MOVE, source="b/c.txt", target="a.txt/c.txt")))
    assert res.has_errors


def test_folder_merge_is_warning(tmp_path):
    root = make_tree(tmp_path / "R", {"Fotos": {"1.jpg": "1"}, "Bilder": {"2.jpg": "2"}})
    snap = scan_folder(root)
    res = resolve_plan(snap, _plan(Operation("op1", RENAME, source="Fotos", target="Bilder")))
    assert not res.has_errors
    assert any(i.level == WARNING and "zusammengeführt" in i.message for i in res.issues)
    bilder = next(c for c in res.root.children if c.name == "Bilder")
    assert bilder.status == MERGED
    assert {c.name for c in bilder.children} == {"1.jpg", "2.jpg"}


def test_swap_names_is_valid(tmp_path):
    root = make_tree(tmp_path / "R", {"a.txt": "A", "b.txt": "B"})
    snap = scan_folder(root)
    res = resolve_plan(
        snap,
        _plan(
            Operation("op1", RENAME, source="a.txt", target="b.txt"),
            Operation("op2", RENAME, source="b.txt", target="a.txt"),
        ),
    )
    assert not res.has_errors


def test_child_op_inside_moved_folder(tmp_path):
    root = make_tree(tmp_path / "R", {"alt": {"x.txt": "1", "y.txt": "2"}})
    snap = scan_folder(root)
    res = resolve_plan(
        snap,
        _plan(
            Operation("op1", RENAME, source="alt", target="Neu"),
            Operation("op2", MOVE, source="alt/x.txt", target="Anderswo/x.txt"),
        ),
    )
    assert not res.has_errors
    assert res.file_map["alt/x.txt"] == "Anderswo/x.txt"
    assert res.file_map["alt/y.txt"] == "Neu/y.txt"
    assert res.resolve_any("alt/neu_hinzugekommen.txt") == "Neu/neu_hinzugekommen.txt"
    assert "Anderswo" in res.created_dirs


def test_target_in_vacated_folder_warns(tmp_path):
    root = make_tree(tmp_path / "R", {"Bilder": {"a.jpg": "1"}, "b.jpg": "2"})
    snap = scan_folder(root)
    res = resolve_plan(
        snap,
        _plan(
            Operation("op1", RENAME, source="Bilder", target="Images"),
            Operation("op2", MOVE, source="b.jpg", target="Bilder/b.jpg"),
        ),
    )
    assert any(i.op_id == "op2" and i.level == WARNING for i in res.issues)


def test_rejected_ops_are_ignored(example_project):
    snap = scan_folder(example_project)
    op = Operation("op1", MOVE, source="datei1.txt", target="Dokumente/datei1.txt", accepted=False)
    res = resolve_plan(snap, _plan(op))
    assert res.file_map["datei1.txt"] == "datei1.txt"
    assert res.created_dirs == []


def test_long_path_warning(tmp_path):
    root = make_tree(tmp_path / "R", {"a.txt": "1"})
    snap = scan_folder(root)
    res = resolve_plan(
        snap, _plan(Operation("op1", MOVE, source="a.txt", target="x" * 100 + "/" + "y" * 100 + "/a.txt")),
        dest_root="C:\\" + "z" * 80,
    )
    assert any("260" in i.message for i in res.issues)
