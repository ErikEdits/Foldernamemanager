from __future__ import annotations

import json
from pathlib import Path

from ordnerorganisator.cli import main

from conftest import snapshot_files


def test_cli_end_to_end(example_project, tmp_path, capsys):
    before = snapshot_files(example_project)
    assert main(["scan", str(example_project)]) == 0
    assert "Downloads neu/" in capsys.readouterr().out

    plan_file = tmp_path / "plan.json"
    assert main(["analyse", str(example_project), "-o", str(plan_file)]) == 0
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    assert data["format"] == "ki-ordnerorganisator-plan" and data["operations"]

    out_dir = tmp_path / "export"
    out_dir.mkdir()
    assert main(["export", str(example_project), "-o", str(out_dir)]) == 0
    exported = list(out_dir.glob("*.txt"))
    assert len(exported) == 1 and "Downloads neu/setup.exe" in exported[0].read_text(encoding="utf-8")

    assert main(["pruefen", str(example_project), str(plan_file)]) == 0
    assert "Downloads_Neu/" in capsys.readouterr().out

    desk = tmp_path / "Desktop"
    desk.mkdir()
    assert main(["ausfuehren", str(example_project), str(plan_file), "--ziel", str(desk), "--ja"]) == 0
    out = capsys.readouterr().out
    assert "Fertig" in out
    copy = desk / "MeinProjekt_Organisiert"
    assert "Dokumente/Final_v2.pdf" in snapshot_files(copy)
    assert snapshot_files(example_project) == before

    assert main(["vorgaenge"]) == 0
    run_id = capsys.readouterr().out.split()[0]
    assert main(["rueckgaengig", run_id]) == 0
    assert snapshot_files(copy) == before


def test_cli_rejects_invalid_plan(example_project, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"operations": [
        {"type": "delete", "source": "datei1.txt"},
        {"type": "move", "source": "datei1.txt", "target": "C:/Windows/x.txt"},
    ]}), encoding="utf-8")
    assert main(["pruefen", str(example_project), str(bad)]) == 0
    out = capsys.readouterr().out
    assert "2 Anweisung(en) wurden zurückgewiesen" in out
    assert main(["pruefen", str(example_project), str(Path(tmp_path / "fehlt.json"))]) == 2
