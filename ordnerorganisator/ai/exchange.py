"""Cloud-KI-Übergabe ohne API-Key: Analysepaket exportieren und Antwort importieren."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..plan import Plan, PlanFormatError, plan_from_dict
from ..redaction import redact
from ..windows_names import sanitize_name
from .prompt import AnalysisPackage

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.S)


def package_filename(root_name: str, pkg: AnalysisPackage, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    part = f"_Teil{pkg.part}von{pkg.parts}" if pkg.parts > 1 else ""
    return sanitize_name(f"KI-Ordnerorganisator_Analysepaket_{root_name}_{stamp}{part}.txt")


def export_packages(packages: list[AnalysisPackage], root_name: str, dest_dir: str | Path) -> list[Path]:
    """Speichert die Pakete als Textdateien (z. B. auf dem Desktop) und gibt die Pfade zurück."""
    dest = Path(dest_dir)
    out = []
    now = datetime.now()
    for pkg in packages:
        path = dest / package_filename(root_name, pkg, now)
        n = 2
        while path.exists():
            path = path.with_name(f"{path.stem}_{n}{path.suffix}")
            n += 1
        path.write_text(redact(pkg.text), encoding="utf-8")
        out.append(path)
    return out


def extract_json_text(text: str) -> str:
    """Findet das JSON-Objekt in einer KI-Antwort (auch mit Codeblöcken oder Begleittext)."""
    text = text.lstrip("﻿").strip()
    if not text:
        raise PlanFormatError("Die Antwort ist leer.")
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    candidates = _FENCE_RE.findall(text)
    for c in candidates:
        c = c.strip()
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, (dict, list)):
            return c
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            json.loads(snippet)
            return snippet
        except json.JSONDecodeError as exc:
            raise PlanFormatError(
                f"Die Antwort enthält kein gültiges JSON (Zeile {exc.lineno}, Spalte {exc.colno}: {exc.msg}). "
                "Bitten Sie die KI, ausschließlich das JSON-Objekt auszugeben."
            ) from exc
    raise PlanFormatError("In der Antwort wurde kein JSON-Objekt gefunden.")


def import_plan_text(text: str, origin: str = "import", expected_package_id: str | None = None) -> Plan:
    data = json.loads(extract_json_text(text))
    plan = plan_from_dict(data, origin=origin)
    if expected_package_id and plan.package_id and plan.package_id != expected_package_id:
        plan.import_notes.append(
            "Die package_id passt nicht zur aktuellen Analyse – der Plan stammt evtl. aus einem anderen Ordner "
            "oder einer älteren Analyse. Alle Anweisungen werden trotzdem gegen den aktuellen Ordner geprüft."
        )
    return plan


def import_plan_files(paths: list[str | Path], expected_package_id: str | None = None) -> Plan:
    """Importiert einen oder mehrere (Teil-)Pläne und führt sie zusammen."""
    merged: Plan | None = None
    for p in paths:
        path = Path(p)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252")
        try:
            plan = import_plan_text(text, origin=f"import:{path.name}", expected_package_id=expected_package_id)
        except PlanFormatError as exc:
            raise PlanFormatError(f"{path.name}: {exc}") from exc
        if merged is None:
            merged = plan
        else:
            merged.merge(plan)
    if merged is None:
        raise PlanFormatError("Es wurde keine Datei ausgewählt.")
    return merged
