"""Kommandozeile – dieselbe sichere Logik wie die Oberfläche, z. B. für Automatisierung.

Beispiele::

    python -m ordnerorganisator scan "C:\\Users\\Erik\\MeinProjekt"
    python -m ordnerorganisator analyse "C:\\…\\MeinProjekt" -o plan.json
    python -m ordnerorganisator export "C:\\…\\MeinProjekt"          # Analysepaket für ChatGPT
    python -m ordnerorganisator pruefen "C:\\…\\MeinProjekt" plan.json
    python -m ordnerorganisator ausfuehren "C:\\…\\MeinProjekt" plan.json
    python -m ordnerorganisator vorgaenge | fortsetzen ID | rueckgaengig ID
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .ai.base import AnalysisRequest
from .ai.engine import BuiltinEngine
from .ai.exchange import export_packages, import_plan_files
from .ai.prompt import build_packages
from .executor import (
    PHASE_LABELS_DE,
    ExecutionCancelled,
    Executor,
    RunState,
    create_run,
    list_runs,
    prepare_run,
)
from .models import Entry, format_size
from .paths import desktop_dir
from .plan import PlanFormatError
from .planner import ERROR, INFO, STATUS_LABELS_DE, WARNING, FinalNode, auto_reject_invalid
from .scanner import ScanError, scan_folder
from .settings import AppSettings, UserRules


def _print_tree(entry: Entry, prefix: str = "", limit: list[int] | None = None) -> None:
    limit = limit if limit is not None else [400]
    children = entry.children
    for i, c in enumerate(children):
        if limit[0] <= 0:
            print(prefix + "└── …")
            return
        limit[0] -= 1
        last = i == len(children) - 1
        mark = "└── " if last else "├── "
        extra = "  [Verknüpfung]" if c.is_link else ""
        print(f"{prefix}{mark}{c.name}{'/' if c.is_dir else ''}{extra}")
        if c.is_dir:
            _print_tree(c, prefix + ("    " if last else "│   "), limit)


def _print_final(node: FinalNode, prefix: str = "") -> None:
    for i, c in enumerate(node.children):
        last = i == len(node.children) - 1
        status = "" if c.status in ("unchanged", "inherited") else f"   ← {STATUS_LABELS_DE[c.status]}"
        print(f"{prefix}{'└── ' if last else '├── '}{c.name}{'/' if c.is_dir else ''}{status}")
        if c.is_dir:
            _print_final(c, prefix + ("    " if last else "│   "))


def _rules_from_args(args) -> UserRules:
    rules = AppSettings.load().rules
    if getattr(args, "sprache", None):
        rules.language = args.sprache
    if getattr(args, "stil", None):
        rules.naming_style = args.stil
    if getattr(args, "modus", None):
        rules.project_mode = args.modus
    return rules


def cmd_scan(args) -> int:
    snap = scan_folder(args.ordner)
    print(f"{snap.root_path}: {snap.dir_count} Ordner, {snap.file_count} Dateien, {format_size(snap.total_size)}")
    print(f"{snap.root_name}/")
    _print_tree(snap.root)
    for rel, msg in snap.skipped:
        print(f"  Hinweis: {rel}: {msg}")
    return 0


def cmd_analyse(args) -> int:
    snap = scan_folder(args.ordner)
    plan = BuiltinEngine().analyze(AnalysisRequest(snap, _rules_from_args(args)))
    print(plan.summary)
    for op in plan.operations:
        mark = "[x]" if op.accepted else "[ ]"
        src = f"{op.source} → " if op.source else ""
        print(f"  {mark} {op.id} {op.kind_label()}: {src}{op.target}   ({op.reason})")
    for w in plan.warnings:
        print(f"  Warnung: {w}")
    for q in plan.questions:
        print(f"  Frage: {q}")
    if args.ausgabe:
        Path(args.ausgabe).write_text(plan.to_json(), encoding="utf-8")
        print(f"Plan gespeichert: {args.ausgabe}")
    return 0


def cmd_export(args) -> int:
    snap = scan_folder(args.ordner)
    settings = AppSettings.load()
    req = AnalysisRequest(
        snap, _rules_from_args(args), include_hidden=args.versteckte,
        max_package_chars=args.max_zeichen or settings.max_package_chars,
    )
    packages, privacy = build_packages(req, for_chat=True)
    print("Das Analysepaket enthält:")
    for line in privacy.lines():
        print(f"  • {line}")
    files = export_packages(packages, snap.root_name, args.ausgabe or desktop_dir())
    for f in files:
        print(f"Gespeichert: {f}")
    print("Laden Sie die Datei(en) bei ChatGPT o. Ä. hoch und speichern Sie die JSON-Antwort als Datei.")
    return 0


def _load_and_check(args):
    snap = scan_folder(args.ordner)
    plan = import_plan_files(args.plan)
    res, rejected = auto_reject_invalid(snap, plan)
    for note in plan.import_notes:
        print(f"Hinweis: {note}")
    if rejected:
        print(f"{len(rejected)} Anweisung(en) wurden zurückgewiesen:")
        bad = {op.id for op in rejected}
        from .planner import resolve_plan

        for op in rejected:
            op.accepted = True
        check = resolve_plan(snap, plan)
        for op in rejected:
            op.accepted = False
        for issue in check.issues:
            if issue.op_id in bad and issue.level == ERROR:
                print(f"  ✗ {issue.op_id}: {issue.message}")
    for issue in res.issues:
        if issue.level in (WARNING, INFO):
            print(f"  ! {issue.op_id or ''}: {issue.message}")
    return snap, plan, res


def cmd_pruefen(args) -> int:
    snap, plan, res = _load_and_check(args)
    print(f"\nNeue Struktur ({len(plan.accepted_operations())} gültige Änderungen):")
    print(f"{snap.root_name}/")
    _print_final(res.root)
    return 0 if not res.has_errors else 2


def _progress_printer():
    last = [""]

    def show(p):
        pct = int(p.done * 100 / p.total) if p.total else 100
        text = f"\r{'Kopieren' if p.phase == 'copy' else 'Umsetzen'}: {pct:3d} %"
        if text != last[0]:
            sys.stdout.write(text)
            sys.stdout.flush()
            last[0] = text

    return show


def _run_executor(state: RunState) -> int:
    try:
        Executor(state, progress=_progress_printer()).run_all()
    except ExecutionCancelled:
        print(f"\nAbgebrochen. Fortsetzen mit: fortsetzen {state.run_id}")
        return 1
    except KeyboardInterrupt:
        state.save()
        print(f"\nAbgebrochen. Fortsetzen mit: fortsetzen {state.run_id}")
        return 1
    print()
    print(f"Fertig. Kopie: {state.dest_root}")
    print(f"Änderungsprotokoll: {state.report_file}")
    if state.copy_errors or state.apply_errors:
        print(f"Achtung: {len(state.copy_errors)} Kopierfehler, {len(state.apply_errors)} fehlgeschlagene Aktionen.")
    return 0


def cmd_ausfuehren(args) -> int:
    snap, plan, res = _load_and_check(args)
    settings = AppSettings.load()
    prep = prepare_run(snap, res, dest_parent=args.ziel or settings.output_dir or None,
                       lang=plan.language or "de")
    counts = res.counts()
    print(f"\nOriginal (bleibt unverändert): {snap.root_path}")
    print(f"Kopie wird erstellt unter:      {prep.dest_root}")
    print(f"Benötigt: {format_size(prep.required_bytes)}, frei: {format_size(prep.free_bytes)}")
    print(f"Änderungen in der Kopie: {counts['renamed']} umbenannt, {counts['moved'] + counts['moved_renamed']} "
          f"verschoben, {counts['new']} neue Ordner")
    for w in prep.warnings:
        print(f"  ! {w}")
    if not prep.ok:
        for e in prep.errors:
            print(f"  ✗ {e}")
        return 2
    if not args.ja:
        answer = input("Kopie erstellen und Änderungen darin umsetzen? [j/N] ").strip().lower()
        if answer not in ("j", "ja", "y", "yes"):
            print("Abgebrochen – es wurde nichts verändert.")
            return 1
    state = create_run(prep, snap, plan, res, lang=plan.language or "de")
    return _run_executor(state)


def cmd_vorgaenge(_args) -> int:
    runs = list_runs()
    if not runs:
        print("Keine Vorgänge vorhanden.")
    for r in runs:
        print(f"{r.run_id}  {PHASE_LABELS_DE.get(r.phase, r.phase):45}  {r.dest_root}")
    return 0


def cmd_fortsetzen(args) -> int:
    return _run_executor(RunState.load(args.vorgang))


def cmd_rueckgaengig(args) -> int:
    state = RunState.load(args.vorgang)
    Executor(state, progress=_progress_printer()).undo()
    print(f"\nRückgängig gemacht. Die Kopie {state.dest_root} hat wieder die ursprüngliche Struktur.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ordnerorganisator", description=f"{APP_NAME} {__version__} – Kommandozeile")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = p.add_subparsers(dest="befehl")

    def rules_args(sp):
        sp.add_argument("--sprache", choices=["auto", "de", "en"])
        sp.add_argument("--stil", choices=["title_underscore", "title_space", "kebab", "snake", "keep"])
        sp.add_argument("--modus", choices=["auto", "general", "java", "minecraft", "video", "photos"])

    sp = sub.add_parser("scan", help="Ordnerstruktur anzeigen")
    sp.add_argument("ordner")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("analyse", aliases=["analyze"], help="Vorschläge der integrierten KI anzeigen")
    sp.add_argument("ordner")
    sp.add_argument("-o", "--ausgabe", help="Plan als JSON speichern")
    rules_args(sp)
    sp.set_defaults(func=cmd_analyse)

    sp = sub.add_parser("export", help="Analysepaket für eine Cloud-KI erstellen")
    sp.add_argument("ordner")
    sp.add_argument("-o", "--ausgabe", help="Zielordner (Standard: Desktop)")
    sp.add_argument("--versteckte", action="store_true", help="versteckte Dateien aufführen")
    sp.add_argument("--max-zeichen", type=int, help="maximale Größe je Teil")
    rules_args(sp)
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("pruefen", aliases=["validate"], help="Plan prüfen und neue Struktur anzeigen")
    sp.add_argument("ordner")
    sp.add_argument("plan", nargs="+")
    sp.set_defaults(func=cmd_pruefen)

    sp = sub.add_parser("ausfuehren", aliases=["apply"], help="Kopie erstellen und Plan darin umsetzen")
    sp.add_argument("ordner")
    sp.add_argument("plan", nargs="+")
    sp.add_argument("--ziel", help="Ordner, in dem die Kopie entsteht (Standard: Desktop)")
    sp.add_argument("--ja", action="store_true", help="ohne Rückfrage ausführen")
    sp.set_defaults(func=cmd_ausfuehren)

    sp = sub.add_parser("vorgaenge", aliases=["runs"], help="bisherige Vorgänge auflisten")
    sp.set_defaults(func=cmd_vorgaenge)
    sp = sub.add_parser("fortsetzen", aliases=["resume"], help="unterbrochenen Vorgang fortsetzen")
    sp.add_argument("vorgang")
    sp.set_defaults(func=cmd_fortsetzen)
    sp = sub.add_parser("rueckgaengig", aliases=["undo"], help="Änderungen in der Kopie rückgängig machen")
    sp.add_argument("vorgang")
    sp.set_defaults(func=cmd_rueckgaengig)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (ScanError, PlanFormatError, FileNotFoundError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
