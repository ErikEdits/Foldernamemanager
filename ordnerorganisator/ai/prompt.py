"""Erstellt Analysepakete (Aufgabe, Regeln, Antwortformat, Ordnerstruktur) für externe KIs.

Dieselben Pakete werden für die API-Anbindung (OpenRouter, lokale Server)
und für den manuellen Export (Zwischenablage/Datei für ChatGPT & Co.) genutzt.
Große Strukturen werden in mehrere Teile aufgeteilt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..models import Entry, Snapshot
from ..plan import PLAN_FORMAT, PLAN_VERSION
from ..redaction import redact
from ..settings import DATE_FORMATS, NAMING_STYLES, UserRules
from .base import AnalysisRequest
from .engine import resolve_language
from .naming import STYLE_LABELS
from .projecttypes import PROJECT_LABELS_DE, detect_project_type

MIN_BUDGET = 4000


@dataclass
class AnalysisPackage:
    package_id: str
    part: int
    parts: int
    text: str
    entry_count: int
    scope: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"Teil {self.part} von {self.parts}" if self.parts > 1 else "Analysepaket"


@dataclass
class PrivacySummary:
    names: int
    folders: int
    files: int
    hidden_excluded: int
    protected_collapsed: int
    absolute_root: str | None
    content_files: list[str]
    parts: int
    chars: int

    def lines(self) -> list[str]:
        out = [
            f"{self.folders} Ordnernamen und {self.files} Dateinamen mit relativen Pfaden und Dateiendungen",
            "Ihre Vorgaben (Sprache, Namenskonvention, Datumsformat, eigene Regeln)",
        ]
        if self.absolute_root:
            out.append(f"Vollständiger Pfad des Ordners: {self.absolute_root}")
        else:
            out.append("Kein vollständiger Pfad (nur der Ordnername) – Benutzername u. Ä. bleiben verborgen")
        if self.content_files:
            out.append(f"Inhaltsauszüge aus {len(self.content_files)} ausgewählten Datei(en)")
        else:
            out.append("Keine Dateiinhalte")
        if self.hidden_excluded:
            out.append(f"{self.hidden_excluded} versteckte/System-Dateien werden nicht aufgeführt")
        if self.protected_collapsed:
            out.append(f"{self.protected_collapsed} geschützte Ordner (z. B. .git) nur zusammengefasst")
        out.append("Niemals enthalten: Passwörter, API-Keys, Zugangsdaten")
        return out


def package_id_for(snapshot: Snapshot) -> str:
    h = hashlib.sha1(f"{snapshot.root_path}|{snapshot.scanned_at}|{snapshot.file_count}".encode()).hexdigest()
    return h[:12]


# ---------------------------------------------------------------------------
# Auflistung
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    label: str
    lines: list[str]

    @property
    def size(self) -> int:
        return sum(len(line) + 1 for line in self.lines)


def _entry_line(e: Entry) -> str:
    return f"{e.rel_path}/" if e.is_dir else e.rel_path


def _visible(e: Entry, snapshot: Snapshot, include_hidden: bool) -> bool:
    if e.is_link:
        return False
    if not include_hidden and e.is_special:
        return False
    return True


def _dir_groups(d: Entry, snapshot: Snapshot, include_hidden: bool, budget: int, stats: dict) -> list[_Group]:
    """Zeilen eines Ordners (inkl. Unterordner) – bei Bedarf in Gruppen ≤ budget geteilt."""
    own: list[str] = []
    if d.rel_path:
        if snapshot.is_protected(d.rel_path):
            stats["protected"] += 1
            return [_Group(d.rel_path, [f"{d.rel_path}/   [geschützt – {d.file_count} Datei(en), nicht verändern]"])]
        own.append(_entry_line(d))
    sub_groups: list[_Group] = []
    for c in d.children:
        if not _visible(c, snapshot, include_hidden):
            if not c.is_link:
                stats["hidden"] += 1 if not c.is_dir else c.file_count + 1
            continue
        if c.is_dir:
            sub_groups.extend(_dir_groups(c, snapshot, include_hidden, budget, stats))
        else:
            own.append(_entry_line(c))
            stats["files"] += 1
    if d.rel_path:
        stats["folders"] += 1
    total = sum(len(x) + 1 for x in own) + sum(g.size for g in sub_groups)
    if total <= budget:
        return [_Group(d.rel_path or ".", own + [line for g in sub_groups for line in g.lines])]
    groups: list[_Group] = []
    chunk: list[str] = []
    size = 0
    for line in own:
        if size + len(line) + 1 > budget and chunk:
            groups.append(_Group(d.rel_path or ".", chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        groups.append(_Group(d.rel_path or ".", chunk))
    groups.extend(sub_groups)
    return groups


def _pack(groups: list[_Group], budget: int) -> list[list[_Group]]:
    parts: list[list[_Group]] = []
    cur: list[_Group] = []
    size = 0
    for g in groups:
        if cur and size + g.size > budget:
            parts.append(cur)
            cur, size = [], 0
        cur.append(g)
        size += g.size
    if cur or not parts:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

EXAMPLE_PLAN = {
    "format": PLAN_FORMAT,
    "version": PLAN_VERSION,
    "package_id": "<package_id übernehmen>",
    "summary": "Kurze Zusammenfassung der vorgeschlagenen Organisation.",
    "operations": [
        {"id": "op1", "type": "create_folder", "target": "Dokumente", "reason": "Sammelordner für Dokumente"},
        {"id": "op2", "type": "rename", "source": "Downloads neu", "target": "Downloads_Neu",
         "reason": "Einheitliche Schreibweise", "confidence": 0.9},
        {"id": "op3", "type": "move", "source": "final_final_v2.pdf", "target": "Dokumente/Final_v2.pdf",
         "reason": "Dokument einsortiert, doppeltes „final“ entfernt", "confidence": 0.8},
    ],
    "warnings": ["Hinweise auf Risiken, z. B. Verknüpfungen in Projektdateien"],
    "questions": ["Rückfragen bei Unsicherheit, z. B.: Was enthält „datei1.txt“?"],
}


def _rules_text(rules: UserRules, lang: str, mode_label: str) -> str:
    lang_txt = {"de": "Deutsch", "en": "Englisch"}[lang]
    style_txt = NAMING_STYLES.get(rules.naming_style, rules.naming_style)
    lines = [
        f"- Sprache für Ordner- und Dateinamen: {lang_txt}"
        + (" (bestehende Namen dürfen übersetzt werden, wenn das die Struktur einheitlicher macht)"
           if rules.translate_existing else " (bestehende Namen nicht übersetzen)"),
        f"- Namenskonvention: {style_txt}",
        f"- Datumsformat in Namen: {DATE_FORMATS.get(rules.date_format, rules.date_format)}",
        f"- Projektart: {mode_label}",
        "- Dateiendungen kleinschreiben." if rules.lowercase_extensions else "- Dateiendungen nicht verändern.",
        "- Umlaute in neuen Namen ersetzen (ä → ae, ö → oe, ü → ue, ß → ss)." if rules.transliterate_umlauts else
        "- Umlaute sind erlaubt.",
    ]
    if not rules.rename_files:
        lines.append("- Dateien NICHT umbenennen (nur verschieben).")
    if not rules.rename_folders:
        lines.append("- Bestehende Ordner NICHT umbenennen.")
    if not rules.group_by_type:
        lines.append("- Dateien NICHT nach Dateityp in Unterordner einsortieren.")
    if rules.preferred_folder_names:
        prefs = ", ".join(f"{k} → „{v}“" for k, v in rules.preferred_folder_names.items() if v)
        if prefs:
            lines.append(f"- Bevorzugte Ordnernamen: {prefs}")
    if rules.custom_instructions.strip():
        lines.append("- Eigene Regeln des Benutzers:\n" + "\n".join(
            "    " + line for line in rules.custom_instructions.strip().splitlines()))
    return "\n".join(lines)


def instructions_text(rules: UserRules, lang: str, mode_label: str, for_chat: bool) -> str:
    style_example = STYLE_LABELS.get(rules.naming_style, "wie bisher")
    answer_hint = (
        "Antworte ausschließlich mit EINEM JSON-Objekt in einem ```json-Codeblock – ohne weiteren Text davor "
        "oder danach. Der Benutzer speichert die Antwort als .json-Datei und importiert sie wieder."
        if for_chat
        else "Antworte ausschließlich mit EINEM gültigen JSON-Objekt (kein Markdown, kein Fließtext)."
    )
    return f"""# Auftrag: Ordnerstruktur organisieren (KI-Ordnerorganisator)

Du hilfst, einen unübersichtlichen Ordner logisch und einheitlich zu organisieren.
Du erhältst unten die vollständige Liste der Ordner und Dateien (nur Namen und relative Pfade).
Erstelle daraus einen **Organisationsplan**: bessere Ordner- und Dateinamen, sinnvolle Unterordner
und eine nachvollziehbare Einsortierung der Dateien.

## Wichtige Regeln
1. Du lieferst NUR einen Plan. Das Programm prüft ihn, zeigt ihn dem Benutzer und setzt ihn erst nach
   dessen Zustimmung um – und zwar ausschließlich in einer Kopie. Das Original bleibt unverändert.
2. Erlaubte Operationen: "create_folder", "rename", "move". Löschen, Überschreiben oder Zusammenführen
   von Dateien ist VERBOTEN und wird abgelehnt.
3. Alle Pfade sind relativ zum Hauptordner, mit "/" getrennt, ohne Laufwerk, ohne führendes "/" und ohne "..".
4. "source" muss exakt (Zeichen für Zeichen) ein Pfad aus der Liste sein. "source" bezieht sich immer auf
   die URSPRÜNGLICHE Struktur, auch wenn ein übergeordneter Ordner umbenannt wird.
5. "target" ist der vollständige NEUE Pfad inklusive Namen – bezogen auf die NEUE Struktur. Wird ein Ordner
   umbenannt, wandert sein Inhalt automatisch mit; für den Inhalt sind dann keine eigenen Anweisungen nötig.
   Elternordner von Zielen werden automatisch angelegt; "create_folder" nur für zusätzlich leere Ordner.
6. Windows-Regeln für Namen: keine Zeichen < > : " / \\ | ? *, kein Leerzeichen oder Punkt am Ende,
   keine reservierten Namen (CON, PRN, AUX, NUL, COM1–COM9, LPT1–LPT9). Groß-/Kleinschreibung zählt
   nicht als Unterschied – zwei Ziele dürfen sich nicht nur darin unterscheiden.
7. Du siehst nur Namen, keine Inhalte (außer ausdrücklich freigegebenen Auszügen). Behaupte nie, den Inhalt
   einer Datei zu kennen. Ist ein Name nichtssagend (z. B. "datei1.txt"), behalte ihn bei und stelle eine
   Frage in "questions" – erfinde keine Bedeutung.
8. Dateiendungen nie inhaltlich ändern (höchstens kleinschreiben).
9. Software-Projekte (z. B. mit pom.xml, build.gradle, package.json, .git) nicht umstrukturieren –
   Build-Werkzeuge erwarten feste Pfade. Als [geschützt] markierte Ordner niemals verändern.
10. Gib für jede Operation eine kurze Begründung ("reason") und eine Sicherheit ("confidence", 0 bis 1) an.
    Unsichere Vorschläge mit confidence < 0.5 kennzeichnen.
11. Weniger ist mehr: Schlage nur Änderungen vor, die die Struktur klar verbessern.
12. Übernimm den Wert von "package_id" unverändert in deine Antwort.

## Vorgaben des Benutzers
{_rules_text(rules, lang, mode_label)}
- Beispiel für die Namenskonvention: {style_example}

## Antwortformat
{answer_hint}
Format (Beispiel):
```json
{json.dumps(EXAMPLE_PLAN, ensure_ascii=False, indent=2)}
```
"""


def build_packages(request: AnalysisRequest, for_chat: bool = True) -> tuple[list[AnalysisPackage], PrivacySummary]:
    snap = request.snapshot
    rules = request.rules
    lang, _sure = resolve_language(rules, snap)
    mode = rules.project_mode
    if mode == "auto":
        det = detect_project_type(snap)
        mode_label = f"{det.label} (automatisch erkannt: {'; '.join(det.reasons)})"
    else:
        mode_label = PROJECT_LABELS_DE.get(mode, mode)
    pid = package_id_for(snap)
    head = instructions_text(rules, lang, mode_label, for_chat)

    content_block = ""
    content_files = []
    if request.contents:
        blocks = []
        for rel, info in request.contents.items():
            snippet = (getattr(info, "title", "") or "").strip()
            excerpt = (getattr(info, "excerpt", "") or "").strip()[:500]
            pkg = getattr(info, "java_package", "")
            parts = []
            if snippet:
                parts.append(f"Titel/Überschrift: {snippet}")
            if pkg:
                parts.append(f"Java-Paket: {pkg}")
            if excerpt:
                parts.append("Auszug:\n" + "\n".join("    " + line for line in excerpt.splitlines()[:12]))
            if parts:
                blocks.append(f"### {rel}\n" + "\n".join(parts))
                content_files.append(rel)
        if blocks:
            content_block = (
                "\n## Inhaltsauszüge (vom Benutzer ausdrücklich freigegeben)\n"
                "Nur diese Dateien wurden inhaltlich gelesen; alle anderen kennst du nur dem Namen nach.\n\n"
                + "\n\n".join(blocks) + "\n"
            )

    stats = {"files": 0, "folders": 0, "hidden": 0, "protected": 0}
    budget = max(MIN_BUDGET, request.max_package_chars - len(head) - len(content_block) - 600)
    groups = _dir_groups(snap.root, snap, request.include_hidden, budget, stats)
    groups = [g for g in groups if g.lines]
    packed = _pack(groups, budget)

    root_desc = f"„{snap.root_name}“"
    if request.include_absolute_root:
        root_desc += f" (vollständiger Pfad: {snap.root_path})"

    packages: list[AnalysisPackage] = []
    for i, part_groups in enumerate(packed, 1):
        lines = [line for g in part_groups for line in g.lines]
        scope = sorted({g.label for g in part_groups})
        part_note = ""
        if len(packed) > 1:
            part_note = (
                f"\nDies ist **Teil {i} von {len(packed)}**. Erstelle den Plan nur für die Einträge dieses Teils. "
                "Verwende für gleichartige Zielordner in allen Teilen dieselben Namen.\n"
            )
        body = (
            f"{head}\n## Ordnerstruktur\n"
            f"package_id: {pid}\n"
            f"Hauptordner: {root_desc}\n"
            f"Einträge in diesem Teil: {len(lines)} (Ordner enden mit „/“)\n"
            f"{part_note}\n"
            "```text\n" + "\n".join(lines) + "\n```\n"
            f"{content_block if i == 1 else ''}"
        )
        packages.append(AnalysisPackage(pid, i, len(packed), redact(body), len(lines), scope))

    summary = PrivacySummary(
        names=stats["files"] + stats["folders"],
        folders=stats["folders"],
        files=stats["files"],
        hidden_excluded=stats["hidden"],
        protected_collapsed=stats["protected"],
        absolute_root=snap.root_path if request.include_absolute_root else None,
        content_files=content_files,
        parts=len(packages),
        chars=sum(len(p.text) for p in packages),
    )
    return packages, summary


def estimate_tokens(text_or_chars: str | int) -> int:
    n = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars)
    return max(1, int(n / 3.5))


SYSTEM_PROMPT = (
    "Du bist eine auf Datei- und Ordnerorganisation spezialisierte KI. Du antwortest ausschließlich mit "
    "einem gültigen JSON-Organisationsplan im vorgegebenen Format und hältst dich strikt an alle Regeln."
)
