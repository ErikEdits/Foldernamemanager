"""Prüft einen Organisationsplan und berechnet die daraus entstehende Zielstruktur.

Der Planer arbeitet rein rechnerisch auf dem Snapshot – es werden keine
Dateien angefasst. Er erkennt u. a.:

* fehlende Quellen, ungültige Windows-Namen, unerlaubte Pfade,
* doppelte Zielnamen (wie unter Windows ohne Groß-/Kleinschreibung),
* Konflikte zwischen Dateien und Ordnern, Ordner in sich selbst verschieben,
* mehrere Anweisungen für dieselbe Quelle, geänderte Dateiendungen,
* zu lange Pfade.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Entry, Snapshot
from .plan import CREATE_FOLDER, Operation, Plan
from .windows_names import (
    MAX_PATH,
    is_within,
    join_rel,
    name_of,
    name_problems,
    parent_of,
    path_key,
    split_ext,
)

ERROR = "error"
WARNING = "warning"
INFO = "info"

# Status von Einträgen der Zielstruktur
UNCHANGED = "unchanged"
RENAMED = "renamed"
MOVED = "moved"
MOVED_RENAMED = "moved_renamed"
INHERITED = "inherited"  # mit übergeordnetem Ordner mitgewandert
NEW = "new"
MERGED = "merged"

STATUS_LABELS_DE = {
    UNCHANGED: "unverändert",
    RENAMED: "umbenannt",
    MOVED: "verschoben",
    MOVED_RENAMED: "verschoben + umbenannt",
    INHERITED: "mit Ordner verschoben",
    NEW: "neu",
    MERGED: "zusammengeführt",
}


@dataclass
class Issue:
    level: str
    message: str
    op_id: str | None = None


@dataclass
class FinalNode:
    rel_path: str
    name: str
    is_dir: bool
    status: str = UNCHANGED
    origins: list[str] = field(default_factory=list)
    op_id: str | None = None
    size: int = 0
    children: list["FinalNode"] = field(default_factory=list)


@dataclass
class Resolution:
    snapshot: Snapshot
    dir_map: dict[str, str]
    file_map: dict[str, str]
    explicit: dict[str, Operation]
    created_dirs: list[str]
    explicit_created: list[str]
    issues: list[Issue]
    root: FinalNode

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def has_errors(self) -> bool:
        return any(i.level == ERROR for i in self.issues)

    def issues_for(self, op_id: str) -> list[Issue]:
        return [i for i in self.issues if i.op_id == op_id]

    def file_moves(self) -> list[tuple[str, str]]:
        """(ursprünglicher Pfad, neuer Pfad) für alle Dateien, deren Pfad sich ändert."""
        out = []
        for key, final in self.file_map.items():
            orig = self.snapshot.entries[key].rel_path
            if orig != final:
                out.append((orig, final))
        return out

    def changed_dirs(self) -> list[tuple[str, str]]:
        out = []
        for key, final in self.dir_map.items():
            if key == "":
                continue
            orig = self.snapshot.entries[key].rel_path
            if orig != final:
                out.append((orig, final))
        return out

    def all_final_dirs(self) -> list[str]:
        dirs = {path_key(p): p for p in self.dir_map.values() if p}
        for p in self.created_dirs:
            dirs.setdefault(path_key(p), p)
        return sorted(dirs.values(), key=lambda p: (p.count("/"), p.casefold()))

    def resolve_any(self, orig_rel: str) -> str:
        """Neuer Pfad für einen beliebigen ursprünglichen Pfad.

        Funktioniert auch für Dateien, die erst nach der Analyse hinzugekommen
        sind: Sie wandern mit ihrem (ggf. umbenannten) Ordner mit.
        """
        key = path_key(orig_rel)
        if key in self.file_map:
            return self.file_map[key]
        if key in self.dir_map:
            return self.dir_map[key]
        parts = orig_rel.split("/")
        for i in range(len(parts) - 1, -1, -1):
            anc = "/".join(parts[:i])
            anc_key = path_key(anc)
            if anc_key in self.dir_map:
                return join_rel(self.dir_map[anc_key], *parts[i:])
        return orig_rel

    def counts(self) -> dict[str, int]:
        c = {RENAMED: 0, MOVED: 0, MOVED_RENAMED: 0, NEW: 0, MERGED: 0}

        def walk(n: FinalNode) -> None:
            if n.status in c:
                c[n.status] += 1
            for ch in n.children:
                walk(ch)

        walk(self.root)
        return c


def _responsible_op(snapshot: Snapshot, explicit: dict[str, Operation], rel: str) -> Operation | None:
    parts = rel.split("/") if rel else []
    for i in range(len(parts), 0, -1):
        op = explicit.get(path_key("/".join(parts[:i])))
        if op is not None:
            return op
    return None


def resolve_plan(
    snapshot: Snapshot,
    plan: Plan,
    dest_root: str | None = None,
) -> Resolution:
    """Berechnet die Zielstruktur aller *angenommenen* Operationen und prüft sie."""
    issues: list[Issue] = []
    op_order = {op.id: i for i, op in enumerate(plan.operations)}

    def add(level: str, msg: str, op: Operation | None = None) -> None:
        issues.append(Issue(level, msg, op.id if op else None))

    explicit: dict[str, Operation] = {}
    create_ops: list[Operation] = []

    for op in plan.operations:
        if not op.accepted:
            continue
        if op.parse_errors:
            for err in op.parse_errors:
                add(ERROR, err, op)
            continue
        bad_target = False
        for seg in op.target.split("/"):
            for problem in name_problems(seg):
                add(ERROR, f"Ungültiger Name „{seg}“: {problem}", op)
                bad_target = True
        if bad_target:
            continue
        if op.type == CREATE_FOLDER:
            create_ops.append(op)
            continue
        entry = snapshot.get(op.source or "")
        if entry is None:
            add(ERROR, f"Die Quelle „{op.source}“ existiert im gewählten Ordner nicht.", op)
            continue
        if entry.rel_path == "":
            add(ERROR, "Der gewählte Ordner selbst kann nicht verschoben werden.", op)
            continue
        if entry.is_link:
            add(ERROR, f"„{entry.rel_path}“ ist eine Verknüpfung (Symlink/Junction) und wird nicht kopiert.", op)
            continue
        if entry.key in explicit:
            other = explicit[entry.key]
            add(ERROR, f"Für „{entry.rel_path}“ gibt es bereits die Anweisung {other.id}.", op)
            continue
        if entry.is_dir and is_within(path_key(op.target), entry.key) and path_key(op.target) != entry.key:
            add(ERROR, f"Der Ordner „{entry.rel_path}“ kann nicht in sich selbst verschoben werden.", op)
            continue
        if snapshot.is_protected(entry.rel_path):
            add(WARNING, f"„{entry.rel_path}“ liegt in einem geschützten Ordner (z. B. .git, node_modules).", op)
        if not entry.is_dir:
            old_ext = split_ext(entry.name)[1]
            new_ext = split_ext(name_of(op.target))[1]
            if old_ext.casefold() != new_ext.casefold():
                add(WARNING, f"Die Dateiendung ändert sich von „{old_ext or '(keine)'}“ zu „{new_ext or '(keine)'}“.", op)
        if op.target == entry.rel_path:
            add(INFO, "Diese Anweisung ändert nichts.", op)
        explicit[entry.key] = op

    # --- Zielpfade aller Einträge berechnen (Eltern vor Kindern) -------------
    dir_map: dict[str, str] = {"": ""}
    file_map: dict[str, str] = {}
    for e in snapshot.root.iter_tree():
        if e.is_link:
            continue
        parent_key = path_key(e.parent_rel)
        if parent_key not in dir_map:  # Elternordner ist eine Verknüpfung o. Ä.
            continue
        op = explicit.get(e.key)
        final = op.target if op is not None else join_rel(dir_map[parent_key], e.name)
        if e.is_dir:
            dir_map[e.key] = final
        else:
            file_map[e.key] = final

    # --- Konflikte -----------------------------------------------------------
    occupants: dict[str, list[tuple[str, Entry]]] = {}
    for key, final in dir_map.items():
        if key == "":
            continue
        occupants.setdefault(path_key(final), []).append(("dir", snapshot.entries[key]))
    for key, final in file_map.items():
        occupants.setdefault(path_key(final), []).append(("file", snapshot.entries[key]))

    def later(a: Operation | None, b: Operation | None) -> Operation | None:
        if a is None:
            return b
        if b is None:
            return a
        return a if op_order.get(a.id, 0) >= op_order.get(b.id, 0) else b

    merged_dirs: set[str] = set()
    for fkey, occ in occupants.items():
        if len(occ) < 2:
            continue
        files = [e for kind, e in occ if kind == "file"]
        dirs = [e for kind, e in occ if kind == "dir"]
        responsible = None
        for _, e in occ:
            responsible = later(responsible, _responsible_op(snapshot, explicit, e.rel_path))
        names = ", ".join(f"„{e.rel_path}“" for _, e in occ)
        final_shown = (file_map.get(occ[0][1].key) or dir_map.get(occ[0][1].key))
        if files and dirs:
            add(ERROR, f"Konflikt: Datei und Ordner hätten denselben Zielpfad „{final_shown}“ ({names}).", responsible)
        elif len(files) > 1:
            add(ERROR, f"Doppelter Zielname „{final_shown}“ für {names}.", responsible)
        else:
            merged_dirs.add(fkey)
            add(WARNING, f"Die Ordner {names} werden zu „{final_shown}“ zusammengeführt.", responsible)

    file_finals = {path_key(p): (k, p) for k, p in file_map.items()}
    dir_final_keys = {path_key(p) for p in dir_map.values()}

    explicit_created: list[str] = []
    for op in create_ops:
        k = path_key(op.target)
        if k in file_finals:
            add(ERROR, f"„{op.target}“ ist bereits als Dateiname vorgesehen – Ordner kann nicht angelegt werden.", op)
            continue
        if k in dir_final_keys:
            add(INFO, f"Der Ordner „{op.target}“ ist ohnehin vorhanden.", op)
        explicit_created.append(op.target)

    # Liegt ein Ziel "in" einer Datei?
    def check_ancestors(final: str, op: Operation | None) -> None:
        parts = final.split("/")
        for i in range(1, len(parts)):
            anc_key = path_key("/".join(parts[:i]))
            if anc_key in file_finals:
                add(ERROR, f"Das Ziel „{final}“ würde innerhalb der Datei „{file_finals[anc_key][1]}“ liegen.", op)
                return

    for key, final in list(dir_map.items()) + list(file_map.items()):
        if key and "/" in final:
            check_ancestors(final, _responsible_op(snapshot, explicit, snapshot.entries[key].rel_path))
    for op in create_ops:
        if "/" in op.target:
            check_ancestors(op.target, op)

    # Ziel innerhalb eines Ordners, der gleichzeitig wegbewegt wird
    for op in explicit.values():
        parts = op.target.split("/")
        for i in range(1, len(parts)):
            anc = "/".join(parts[:i])
            anc_key = path_key(anc)
            src_entry = snapshot.entries.get(anc_key)
            if (
                src_entry is not None
                and src_entry.is_dir
                and path_key(dir_map.get(anc_key, anc)) != anc_key
                and anc_key not in dir_final_keys
            ):
                add(
                    WARNING,
                    f"Das Ziel liegt in „{anc}“, dieser Ordner wird aber umbenannt/verschoben "
                    f"(→ „{dir_map.get(anc_key)}“). „{anc}“ würde neu angelegt.",
                    op,
                )
                break

    # Implizit anzulegende Ordner (Elternordner neuer Ziele)
    created: dict[str, str] = {}
    for p in explicit_created:
        if path_key(p) not in dir_final_keys:
            created.setdefault(path_key(p), p)
    for final in list(file_map.values()) + list(dir_map.values()) + explicit_created:
        parts = final.split("/")
        for i in range(1, len(parts)):
            anc = "/".join(parts[:i])
            k = path_key(anc)
            if k not in dir_final_keys and k not in file_finals:
                created.setdefault(k, anc)

    # Pfadlängen
    if dest_root:
        too_long: dict[str | None, int] = {}
        for key, final in list(file_map.items()) + list(dir_map.items()):
            if key and len(dest_root) + 1 + len(final.replace("/", "\\")) >= MAX_PATH:
                op = _responsible_op(snapshot, explicit, snapshot.entries[key].rel_path)
                too_long[op.id if op else None] = too_long.get(op.id if op else None, 0) + 1
        for op_id, n in too_long.items():
            issues.append(Issue(
                WARNING,
                f"{n} Pfad(e) werden länger als {MAX_PATH} Zeichen. Der Organisator kann damit umgehen, "
                "manche ältere Programme jedoch nicht.",
                op_id,
            ))

    root = _build_final_tree(snapshot, dir_map, file_map, explicit, created, merged_dirs)
    return Resolution(
        snapshot=snapshot,
        dir_map=dir_map,
        file_map=file_map,
        explicit=explicit,
        created_dirs=sorted(created.values(), key=lambda p: (p.count("/"), p.casefold())),
        explicit_created=explicit_created,
        issues=issues,
        root=root,
    )


def _status_for(entry: Entry, final: str, op: Operation | None) -> str:
    if op is None:
        return INHERITED if final != entry.rel_path else UNCHANGED
    same_parent = path_key(parent_of(final)) == path_key(entry.parent_rel)
    same_name = name_of(final) == entry.name
    if same_parent and same_name:
        return UNCHANGED
    if same_parent:
        return RENAMED
    if same_name:
        return MOVED
    return MOVED_RENAMED


def _build_final_tree(
    snapshot: Snapshot,
    dir_map: dict[str, str],
    file_map: dict[str, str],
    explicit: dict[str, Operation],
    created: dict[str, str],
    merged_dirs: set[str],
) -> FinalNode:
    root = FinalNode(rel_path="", name=snapshot.root_name, is_dir=True)
    nodes: dict[str, FinalNode] = {"": root}

    def ensure_dir(path: str) -> FinalNode:
        k = path_key(path)
        if k in nodes:
            return nodes[k]
        parent = ensure_dir(parent_of(path))
        node = FinalNode(rel_path=path, name=name_of(path), is_dir=True, status=NEW)
        nodes[k] = node
        parent.children.append(node)
        return node

    # Ordner zuerst, sortiert nach Tiefe, damit Eltern existieren
    dir_items = sorted(
        ((k, f) for k, f in dir_map.items() if k), key=lambda kv: (kv[1].count("/"), kv[1].casefold())
    )
    for key, final in dir_items:
        entry = snapshot.entries[key]
        fk = path_key(final)
        op = explicit.get(key)
        if fk in nodes:
            node = nodes[fk]
            node.origins.append(entry.rel_path)
            if fk in merged_dirs:
                node.status = MERGED
            continue
        parent = ensure_dir(parent_of(final))
        node = FinalNode(
            rel_path=final, name=name_of(final), is_dir=True,
            status=MERGED if fk in merged_dirs else _status_for(entry, final, op),
            origins=[entry.rel_path], op_id=op.id if op else None,
        )
        nodes[fk] = node
        parent.children.append(node)
    for _k, path in sorted(created.items(), key=lambda kv: kv[1].count("/")):
        ensure_dir(path)
    for key, final in file_map.items():
        entry = snapshot.entries[key]
        op = explicit.get(key)
        parent = ensure_dir(parent_of(final))
        parent.children.append(
            FinalNode(
                rel_path=final, name=name_of(final), is_dir=False,
                status=_status_for(entry, final, op), origins=[entry.rel_path],
                op_id=op.id if op else None, size=entry.size,
            )
        )

    def sort(n: FinalNode) -> None:
        n.children.sort(key=lambda c: (not c.is_dir, c.name.casefold()))
        for c in n.children:
            sort(c)

    sort(root)
    return root


def auto_reject_invalid(snapshot: Snapshot, plan: Plan, dest_root: str | None = None) -> tuple[Resolution, list[Operation]]:
    """Lehnt fehlerhafte Operationen automatisch ab, bis der Plan konfliktfrei ist."""
    rejected: list[Operation] = []
    for _ in range(len(plan.operations) + 2):
        res = resolve_plan(snapshot, plan, dest_root)
        bad_ids = {i.op_id for i in res.issues if i.level == ERROR and i.op_id}
        if not bad_ids:
            return res, rejected
        for op in plan.operations:
            if op.id in bad_ids and op.accepted:
                op.accepted = False
                rejected.append(op)
    return resolve_plan(snapshot, plan, dest_root), rejected
