"""Der Organisationsplan: Datenmodell, JSON-Austauschformat und tolerantes Einlesen.

Austauschformat (siehe ``schema/organisationsplan.schema.json`` und
``docs/plan-format.md``)::

    {
      "format": "ki-ordnerorganisator-plan",
      "version": 1,
      "summary": "…",
      "operations": [
        {"id": "op1", "type": "create_folder", "target": "Dokumente", "reason": "…"},
        {"id": "op2", "type": "move", "source": "datei1.txt",
         "target": "Dokumente/datei1.txt", "reason": "…", "confidence": 0.8},
        {"id": "op3", "type": "rename", "source": "Downloads neu",
         "target": "Downloads_Neu", "reason": "…"}
      ],
      "warnings": ["…"],
      "questions": ["…"]
    }

Alle ``source``-Pfade beziehen sich auf die **ursprüngliche** Struktur,
alle ``target``-Pfade auf die **neue** Struktur – jeweils relativ zum
gewählten Ordner. Löschen ist nicht vorgesehen und wird abgelehnt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .windows_names import PathError, name_of, normalize_rel_path, parent_of

PLAN_FORMAT = "ki-ordnerorganisator-plan"
PLAN_VERSION = 1

CREATE_FOLDER = "create_folder"
RENAME = "rename"
MOVE = "move"
OP_TYPES = (CREATE_FOLDER, RENAME, MOVE)

_TYPE_ALIASES = {
    "create_folder": CREATE_FOLDER, "create": CREATE_FOLDER, "mkdir": CREATE_FOLDER,
    "new_folder": CREATE_FOLDER, "create_directory": CREATE_FOLDER, "createfolder": CREATE_FOLDER,
    "folder": CREATE_FOLDER, "ordner_anlegen": CREATE_FOLDER, "anlegen": CREATE_FOLDER,
    "rename": RENAME, "umbenennen": RENAME, "rename_folder": RENAME, "rename_file": RENAME,
    "move": MOVE, "verschieben": MOVE, "move_file": MOVE, "move_folder": MOVE, "relocate": MOVE,
}

_FORBIDDEN_TYPES = {
    "delete", "remove", "rm", "rmdir", "unlink", "löschen", "loeschen", "trash", "erase",
    "delete_file", "delete_folder", "overwrite", "replace", "merge_delete", "cleanup",
}

TYPE_LABELS_DE = {
    CREATE_FOLDER: "Ordner anlegen",
    RENAME: "Umbenennen",
    MOVE: "Verschieben",
}


class PlanFormatError(ValueError):
    """Der Plan ist kein gültiges JSON oder hat eine unbrauchbare Struktur."""


@dataclass
class Operation:
    id: str
    type: str
    target: str
    source: str | None = None
    reason: str = ""
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    #: Vom Benutzer angenommen (Standard) oder abgelehnt
    accepted: bool = True
    #: Herkunft des Vorschlags (z. B. "builtin", "openrouter:model", "import")
    origin: str = ""
    #: Vom Benutzer geändert
    modified: bool = False
    #: Fehler beim Einlesen (z. B. verbotene Operation) – Operation ist nie ausführbar
    parse_errors: list[str] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return not self.parse_errors

    def kind_label(self) -> str:
        """Beschreibt, was tatsächlich passiert (Umbenennen, Verschieben oder beides)."""
        if self.type == CREATE_FOLDER or not self.source:
            return TYPE_LABELS_DE[CREATE_FOLDER]
        same_parent = parent_of(self.source).casefold() == parent_of(self.target).casefold()
        same_name = name_of(self.source) == name_of(self.target)
        if same_parent and not same_name:
            return "Umbenennen"
        if not same_parent and same_name:
            return "Verschieben"
        if not same_parent and not same_name:
            return "Verschieben + Umbenennen"
        return "Keine Änderung"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.source is not None:
            d["source"] = self.source
        d["target"] = self.target
        if self.reason:
            d["reason"] = self.reason
        if self.confidence is not None:
            d["confidence"] = round(float(self.confidence), 2)
        if self.warnings:
            d["warnings"] = list(self.warnings)
        return d


@dataclass
class Plan:
    operations: list[Operation] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    project_type: str | None = None
    language: str | None = None
    provider: str = ""
    model: str | None = None
    package_id: str | None = None
    #: Hinweise, die beim Einlesen entstanden sind (z. B. Korrekturen)
    import_notes: list[str] = field(default_factory=list)

    def accepted_operations(self) -> list[Operation]:
        return [op for op in self.operations if op.accepted and op.executable]

    def by_id(self, op_id: str) -> Operation | None:
        for op in self.operations:
            if op.id == op_id:
                return op
        return None

    def next_id(self) -> str:
        used = {op.id for op in self.operations}
        i = len(self.operations) + 1
        while f"op{i}" in used:
            i += 1
        return f"op{i}"

    def to_dict(self, only_accepted: bool = False) -> dict[str, Any]:
        ops = self.accepted_operations() if only_accepted else self.operations
        d: dict[str, Any] = {
            "format": PLAN_FORMAT,
            "version": PLAN_VERSION,
            "summary": self.summary,
            "operations": [op.to_dict() for op in ops],
            "warnings": list(self.warnings),
            "questions": list(self.questions),
        }
        if self.project_type:
            d["project_type"] = self.project_type
        if self.language:
            d["language"] = self.language
        if self.package_id:
            d["package_id"] = self.package_id
        return d

    def to_json(self, only_accepted: bool = False) -> str:
        return json.dumps(self.to_dict(only_accepted), ensure_ascii=False, indent=2)

    def merge(self, other: "Plan") -> None:
        """Hängt einen weiteren (Teil-)Plan an, z. B. aus mehreren Analysepaketen."""
        for op in other.operations:
            op.id = self.next_id()
            self.operations.append(op)
        if other.summary:
            self.summary = f"{self.summary}\n{other.summary}".strip()
        self.warnings.extend(w for w in other.warnings if w not in self.warnings)
        self.questions.extend(q for q in other.questions if q not in self.questions)
        self.import_notes.extend(other.import_notes)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif isinstance(v, dict):
                text = v.get("text") or v.get("message") or v.get("question") or json.dumps(v, ensure_ascii=False)
                out.append(str(text))
        return out
    return [str(value)]


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def operation_from_dict(raw: Any, index: int, origin: str = "") -> Operation:
    """Liest eine einzelne Operation tolerant ein. Fehler landen in ``parse_errors``."""
    if not isinstance(raw, dict):
        return Operation(
            id=f"op{index}", type=MOVE, target="", origin=origin,
            parse_errors=[f"Eintrag Nr. {index} ist kein Objekt und wurde ignoriert."],
        )
    op_id = str(raw.get("id") or f"op{index}")
    raw_type = str(_first(raw, "type", "action", "op", "operation") or "").strip()
    type_key = raw_type.casefold().replace("-", "_").replace(" ", "_")
    errors: list[str] = []
    notes: list[str] = []

    source = _first(raw, "source", "from", "src", "old_path", "path_old", "quelle")
    target = _first(raw, "target", "to", "dst", "destination", "new_path", "ziel")
    new_name = _first(raw, "new_name", "newName", "name_new")
    path = _first(raw, "path", "folder", "pfad")

    if type_key in _FORBIDDEN_TYPES:
        errors.append(
            f"Operation „{raw_type}“ ist nicht erlaubt – der Organisator löscht oder überschreibt niemals Dateien."
        )
        op_type = MOVE
    elif type_key in _TYPE_ALIASES:
        op_type = _TYPE_ALIASES[type_key]
    elif not type_key:
        if source and (target or new_name):
            op_type = MOVE
        elif path or target:
            op_type = CREATE_FOLDER
        else:
            op_type = MOVE
            errors.append("Operation ohne Typ und ohne erkennbare Pfade.")
    else:
        op_type = MOVE
        errors.append(f"Unbekannter Operationstyp „{raw_type}“. Erlaubt: create_folder, rename, move.")

    if op_type == CREATE_FOLDER:
        target = target or path
        source = None
    else:
        if source is None and path is not None and (target or new_name):
            source = path
        if target is None and new_name is not None and source is not None:
            target = f"{parent_of(str(source).replace(chr(92), '/').strip('/'))}/{new_name}".strip("/")
            notes.append("Ziel aus „new_name“ abgeleitet.")

    try:
        src_norm = normalize_rel_path(str(source)) if source is not None else None
        if op_type != CREATE_FOLDER and not src_norm:
            errors.append("Es fehlt die Quelle („source“).")
    except PathError as exc:
        src_norm = str(source)
        errors.append(str(exc))

    try:
        tgt_norm = normalize_rel_path(str(target)) if target is not None else ""
        if not tgt_norm:
            errors.append("Es fehlt das Ziel („target“).")
    except PathError as exc:
        tgt_norm = str(target)
        errors.append(str(exc))

    # "rename" mit bloßem Namen: im selben Ordner umbenennen
    if (
        op_type == RENAME
        and src_norm
        and tgt_norm
        and "/" not in tgt_norm
        and "/" in src_norm
        and not errors
    ):
        tgt_norm = f"{parent_of(src_norm)}/{tgt_norm}"
        notes.append("Ziel der Umbenennung als neuer Name im bisherigen Ordner interpretiert.")

    conf = raw.get("confidence", raw.get("sicherheit"))
    confidence: float | None = None
    if conf is not None and not isinstance(conf, bool):
        try:
            value = float(str(conf).strip().rstrip("%"))
            if value > 1.0:  # Prozentangaben (z. B. 80) tolerieren
                value /= 100.0
            confidence = max(0.0, min(1.0, value))
        except (TypeError, ValueError):
            confidence = None

    reason = _first(raw, "reason", "begründung", "begruendung", "explanation", "why", "comment") or ""
    warnings = _as_str_list(_first(raw, "warnings", "warning", "warnung"))
    warnings.extend(notes)
    return Operation(
        id=op_id,
        type=op_type,
        source=src_norm,
        target=tgt_norm,
        reason=str(reason).strip(),
        confidence=confidence,
        warnings=warnings,
        origin=origin,
        parse_errors=errors,
    )


def plan_from_dict(data: Any, origin: str = "import") -> Plan:
    if isinstance(data, list):
        data = {"operations": data}
    if not isinstance(data, dict):
        raise PlanFormatError("Der Plan muss ein JSON-Objekt mit dem Feld „operations“ sein.")
    ops_raw = _first(data, "operations", "operationen", "actions", "changes", "steps", "plan")
    if isinstance(ops_raw, dict):
        ops_raw = _first(ops_raw, "operations", "actions")
    if ops_raw is None:
        raise PlanFormatError("Im Plan fehlt die Liste „operations“.")
    if not isinstance(ops_raw, list):
        raise PlanFormatError("„operations“ muss eine Liste sein.")

    notes: list[str] = []
    fmt = data.get("format")
    if fmt and fmt != PLAN_FORMAT:
        notes.append(f"Unbekannte Formatkennung „{fmt}“ – der Plan wird trotzdem geprüft.")
    version = data.get("version")
    if version not in (None, PLAN_VERSION, str(PLAN_VERSION)):
        notes.append(f"Planversion {version} ist neuer als erwartet ({PLAN_VERSION}).")

    ops = [operation_from_dict(raw, i + 1, origin) for i, raw in enumerate(ops_raw)]
    seen: set[str] = set()
    for i, op in enumerate(ops):
        if op.id in seen:
            op.id = f"op{i + 1}_{len(seen)}"
        seen.add(op.id)

    plan = Plan(
        operations=ops,
        summary=str(_first(data, "summary", "zusammenfassung", "description") or "").strip(),
        warnings=_as_str_list(_first(data, "warnings", "warnungen")),
        questions=_as_str_list(_first(data, "questions", "fragen", "uncertain")),
        project_type=(str(data["project_type"]) if data.get("project_type") else None),
        language=(str(data["language"]) if data.get("language") else None),
        package_id=(str(data["package_id"]) if data.get("package_id") else None),
        provider=origin,
        import_notes=notes,
    )
    return plan


def plan_from_json(text: str, origin: str = "import") -> Plan:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanFormatError(
            f"Die Antwort ist kein gültiges JSON (Zeile {exc.lineno}, Spalte {exc.colno}: {exc.msg})."
        ) from exc
    return plan_from_dict(data, origin)
