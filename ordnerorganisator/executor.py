"""Erstellt die Arbeitskopie und setzt genehmigte Änderungen *ausschließlich* darin um.

Sicherheitsprinzipien
---------------------
* Das Original wird nur gelesen. Jede schreibende Dateioperation läuft durch
  :class:`PathGuard`, der Ziele außerhalb der Kopie oder innerhalb des
  Originals mit :class:`SafetyError` ablehnt.
* Es wird nichts gelöscht und nichts überschrieben. Entfernt werden lediglich
  leere Ordner, die durch Umbenennen/Verschieben in der Kopie frei geworden
  sind, sowie eigene unvollständige Teil-Kopien (``*.kiorg-part``).
* Jeder Schritt wird in einem Journal festgehalten. Dadurch lassen sich
  unterbrochene Vorgänge fortsetzen und Änderungen rückgängig machen.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .models import Snapshot, format_size
from .paths import IS_WINDOWS, desktop_dir, is_cloud_synced, long_path, runs_dir
from .plan import Plan
from .planner import Resolution
from .scanner import explain_os_error
from .windows_names import join_rel, parent_of, path_key, sanitize_name, split_ext

STAGING_DIR = ".kiorg-staging"
PART_SUFFIX = ".kiorg-part"
CHUNK = 1024 * 1024

# Phasen eines Vorgangs
PH_CREATED = "created"
PH_COPYING = "copying"
PH_COPIED = "copied"
PH_APPLYING = "applying"
PH_DONE = "done"
PH_UNDOING = "undoing"
PH_UNDONE = "undone"
UNFINISHED_PHASES = (PH_CREATED, PH_COPYING, PH_COPIED, PH_APPLYING, PH_UNDOING)

PHASE_LABELS_DE = {
    PH_CREATED: "vorbereitet",
    PH_COPYING: "Kopieren unterbrochen",
    PH_COPIED: "kopiert, Änderungen noch nicht umgesetzt",
    PH_APPLYING: "Umsetzen der Änderungen unterbrochen",
    PH_DONE: "abgeschlossen",
    PH_UNDOING: "Rückgängigmachen unterbrochen",
    PH_UNDONE: "rückgängig gemacht",
}


class SafetyError(RuntimeError):
    """Eine Operation hätte außerhalb der Arbeitskopie geschrieben."""


class ExecutionCancelled(Exception):
    pass


class PreparationError(Exception):
    pass


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _real(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # verschiedene Laufwerke
        return False


class PathGuard:
    """Lässt schreibende Zugriffe nur innerhalb der Arbeitskopie zu."""

    def __init__(self, dest_root: str, source_root: str):
        self.dest = _norm(dest_root)
        self.source = _norm(source_root)
        for d, src in ((self.dest, self.source), (_real(dest_root), _real(source_root))):
            if _inside(d, src) or _inside(src, d):
                raise SafetyError("Original und Kopie dürfen sich nicht überschneiden.")

    def check(self, path: str) -> str:
        # Pfade werden ausschließlich aus dem Kopie-Wurzelordner und geprüften
        # relativen Segmenten (ohne "..") gebildet; die Kopie enthält keine
        # Verknüpfungen. Ein reiner Pfadvergleich ist daher ausreichend.
        real = _norm(path)
        if not _inside(real, self.dest):
            raise SafetyError(f"Sicherheitsstopp: „{path}“ liegt außerhalb der Arbeitskopie.")
        if _inside(real, self.source):
            raise SafetyError(f"Sicherheitsstopp: „{path}“ liegt im Original.")
        return path


# ---------------------------------------------------------------------------
# Abbildung alter Pfade auf neue Pfade (persistierbar)
# ---------------------------------------------------------------------------


@dataclass
class PathMapper:
    dir_map: dict[str, str]  # Schlüssel: path_key(alter Pfad)
    file_map: dict[str, str]
    op_sources: dict[str, str] = field(default_factory=dict)  # path_key(Quelle) -> op_id

    @classmethod
    def from_resolution(cls, res: Resolution) -> "PathMapper":
        return cls(
            dir_map=dict(res.dir_map),
            file_map=dict(res.file_map),
            op_sources={k: op.id for k, op in res.explicit.items()},
        )

    def resolve(self, rel: str) -> str:
        key = path_key(rel)
        if key in self.file_map:
            return self.file_map[key]
        if key in self.dir_map:
            return self.dir_map[key]
        parts = rel.split("/")
        for i in range(len(parts) - 1, -1, -1):
            anc_key = path_key("/".join(parts[:i]))
            if anc_key in self.dir_map:
                return join_rel(self.dir_map[anc_key], *parts[i:])
        return rel

    def responsible_op(self, rel: str) -> str | None:
        parts = rel.split("/")
        for i in range(len(parts), 0, -1):
            op_id = self.op_sources.get(path_key("/".join(parts[:i])))
            if op_id:
                return op_id
        return None


# ---------------------------------------------------------------------------
# Vorgangszustand (Journal)
# ---------------------------------------------------------------------------


@dataclass
class MoveRecord:
    src: str
    dst: str
    stage: str = ""
    status: str = "pending"  # pending | staged | done | failed
    error: str = ""
    op_id: str | None = None
    final: str = ""  # tatsächlicher Zielpfad (falls ausgewichen werden musste)


@dataclass
class RunState:
    run_id: str
    source_root: str
    dest_root: str
    created_at: str
    phase: str = PH_CREATED
    language: str = "de"
    plan: dict = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)
    mapper: dict = field(default_factory=dict)
    final_dirs: list[str] = field(default_factory=list)
    original_dirs: list[str] = field(default_factory=list)
    vacated_dirs: list[str] = field(default_factory=list)
    moves: list[dict] = field(default_factory=list)
    undo_moves: list[dict] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    removed_dirs: list[str] = field(default_factory=list)
    case_renames: list[list[str]] = field(default_factory=list)
    copy_errors: list[dict] = field(default_factory=list)
    apply_errors: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    updated_at: str = ""

    @property
    def run_dir(self) -> Path:
        return runs_dir() / self.run_id

    @property
    def state_file(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.run_dir / "protokoll.jsonl"

    @property
    def report_file(self) -> Path:
        return self.run_dir / "Aenderungsprotokoll.txt"

    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_file)

    @classmethod
    def load(cls, run_id_or_path: str | os.PathLike) -> "RunState":
        p = Path(run_id_or_path)
        if not p.suffix:
            p = runs_dir() / str(run_id_or_path) / "state.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def log(self, event: str, **data) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        rec = {"zeit": datetime.now().isoformat(timespec="seconds"), "ereignis": event, **data}
        with open(self.log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def get_mapper(self) -> PathMapper:
        return PathMapper(**self.mapper)


def list_runs() -> list[RunState]:
    out = []
    base = runs_dir()
    for d in sorted(base.iterdir(), reverse=True) if base.exists() else []:
        f = d / "state.json"
        if f.is_file():
            try:
                out.append(RunState.load(f))
            except (OSError, ValueError, TypeError):
                continue
    out.sort(key=lambda s: s.created_at, reverse=True)
    return out


def list_unfinished_runs() -> list[RunState]:
    return [r for r in list_runs() if r.phase in UNFINISHED_PHASES]


# ---------------------------------------------------------------------------
# Vorbereitung
# ---------------------------------------------------------------------------


@dataclass
class PreparedRun:
    source_root: str
    dest_root: str
    required_bytes: int
    free_bytes: int
    file_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def copy_folder_name(root_name: str, lang: str = "de") -> str:
    suffix = "Organized" if lang == "en" else "Organisiert"
    return sanitize_name(f"{root_name}_{suffix}")


def unique_path(parent: str, name: str) -> str:
    candidate = os.path.join(parent, name)
    n = 2
    while os.path.lexists(long_path(candidate)):
        candidate = os.path.join(parent, f"{name}_{n}")
        n += 1
    return candidate


def prepare_run(
    snapshot: Snapshot,
    resolution: Resolution,
    dest_parent: str | None = None,
    lang: str = "de",
    dest_name: str | None = None,
) -> PreparedRun:
    """Prüft alles Notwendige, *bevor* irgendetwas geschrieben wird."""
    parent = os.path.abspath(dest_parent or str(desktop_dir()))
    name = sanitize_name(dest_name) if dest_name else copy_folder_name(snapshot.root_name, lang)
    dest_root = unique_path(parent, name)
    required = snapshot.total_size
    prep = PreparedRun(
        source_root=snapshot.root_path,
        dest_root=dest_root,
        required_bytes=required,
        free_bytes=0,
        file_count=snapshot.file_count,
    )
    if resolution.has_errors:
        prep.errors.append("Der Plan enthält noch Fehler. Bitte zuerst alle Konflikte lösen.")
    if not os.path.isdir(long_path(parent)):
        prep.errors.append(f"Der Zielordner „{parent}“ existiert nicht.")
        return prep
    if not os.path.isdir(long_path(snapshot.root_path)):
        prep.errors.append("Der ursprüngliche Ordner ist nicht mehr vorhanden.")
        return prep
    src_n, dst_n = _norm(snapshot.root_path), _norm(dest_root)
    src_r, dst_r = _real(snapshot.root_path), _real(dest_root)
    if _inside(dst_n, src_n) or _inside(dst_r, src_r):
        prep.errors.append(
            "Die Kopie würde innerhalb des Originals entstehen (z. B. wenn der Desktop selbst "
            "oder Ihr Benutzerordner gewählt wurde). Bitte einen anderen Zielordner wählen."
        )
    if _inside(src_n, dst_n) or _inside(src_r, dst_r):
        prep.errors.append("Das Original liegt innerhalb des Zielordners der Kopie.")
    try:
        usage = shutil.disk_usage(long_path(parent))
        prep.free_bytes = usage.free
        margin = max(50 * 1024 * 1024, required // 50)
        if usage.free < required + margin:
            prep.errors.append(
                f"Nicht genügend Speicherplatz: benötigt etwa {format_size(required + margin)}, "
                f"frei sind {format_size(usage.free)}."
            )
    except OSError as exc:
        prep.warnings.append(f"Freier Speicherplatz konnte nicht geprüft werden: {explain_os_error(exc)}")
    if not os.access(long_path(parent), os.W_OK):
        prep.errors.append(f"Keine Schreibberechtigung für „{parent}“.")
    cloud = is_cloud_synced(parent)
    if cloud:
        prep.warnings.append(
            f"Der Zielordner wird mit {cloud} synchronisiert. Die Kopie wird dadurch ggf. "
            "in die Cloud hochgeladen."
        )
    if snapshot.link_count:
        prep.warnings.append(
            f"{snapshot.link_count} Verknüpfung(en) (Symlinks/Junctions) werden nicht kopiert."
        )
    unreadable = [p for p, msg in snapshot.skipped if "Verknüpfung" not in msg]
    if unreadable:
        prep.warnings.append(f"{len(unreadable)} Element(e) konnten nicht gelesen werden und fehlen in der Kopie.")
    return prep


def create_run(
    prepared: PreparedRun,
    snapshot: Snapshot,
    plan: Plan,
    resolution: Resolution,
    lang: str = "de",
) -> RunState:
    if not prepared.ok:
        raise PreparationError("\n".join(prepared.errors))
    if resolution.has_errors:
        raise PreparationError("Der Plan enthält Fehler.")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    mapper = PathMapper.from_resolution(resolution)
    vacated = [snapshot.entries[k].rel_path for k, final in resolution.dir_map.items()
               if k and snapshot.entries[k].rel_path != final]
    state = RunState(
        run_id=run_id,
        source_root=snapshot.root_path,
        dest_root=prepared.dest_root,
        created_at=datetime.now().isoformat(timespec="seconds"),
        language=lang,
        plan=plan.to_dict(only_accepted=True),
        rejected=[op.to_dict() for op in plan.operations if not op.accepted],
        mapper=asdict(mapper),
        final_dirs=resolution.all_final_dirs(),
        original_dirs=[e.rel_path for e in snapshot.dirs() if not e.is_link],
        vacated_dirs=sorted(vacated, key=lambda p: -p.count("/")),
        stats={"geplante_dateien": snapshot.file_count, "bytes": snapshot.total_size},
    )
    state.save()
    for op in plan.operations:
        state.log("geplant", op=op.to_dict(), angenommen=op.accepted)
    for op in plan.accepted_operations():
        state.log("genehmigt", op=op.to_dict())
    return state


# ---------------------------------------------------------------------------
# Ausführung
# ---------------------------------------------------------------------------


@dataclass
class Progress:
    phase: str
    done: int
    total: int
    current: str = ""
    message: str = ""


ProgressFn = Callable[[Progress], None]


def _same_file(src_st: os.stat_result, dst_path: str) -> bool:
    try:
        dst_st = os.stat(long_path(dst_path))
    except OSError:
        return False
    return dst_st.st_size == src_st.st_size and abs(dst_st.st_mtime - src_st.st_mtime) <= 2.0


def _copy_win_attributes(src: str, dst: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        attrs = k32.GetFileAttributesW(long_path(src))
        if attrs != 0xFFFFFFFF:
            keep = attrs & (0x1 | 0x2 | 0x4 | 0x20)  # schreibgeschützt, versteckt, System, Archiv
            if keep:
                k32.SetFileAttributesW(long_path(dst), keep)
    except Exception:
        pass


class Executor:
    def __init__(
        self,
        state: RunState,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ):
        self.state = state
        self.progress = progress or (lambda p: None)
        self.cancel = cancel or threading.Event()
        self.guard = PathGuard(state.dest_root, state.source_root)
        self._last_save = 0.0

    # -- Hilfsfunktionen ----------------------------------------------------
    def _dest(self, rel: str) -> str:
        path = os.path.join(self.state.dest_root, *rel.split("/")) if rel else self.state.dest_root
        return self.guard.check(path)

    def _checkpoint(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_save > 1.0:
            self.state.save()
            self._last_save = now

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            self.state.save()
            raise ExecutionCancelled()

    def _makedirs(self, rel: str) -> None:
        """Legt Ordner in der Kopie an und merkt sich neu angelegte (für Rückgängig)."""
        if not rel:
            return
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            path = self._dest(sub)
            if not os.path.isdir(long_path(path)):
                os.mkdir(long_path(path))
                if self.state.phase in (PH_APPLYING,):
                    self.state.created_dirs.append(sub)

    # -- Phase 1: Kopieren ----------------------------------------------------
    def copy_phase(self) -> list[dict]:
        st = self.state
        if st.phase not in (PH_CREATED, PH_COPYING):
            return st.copy_errors
        st.phase = PH_COPYING
        st.copy_errors = []
        st.log("kopieren_gestartet", quelle=st.source_root, ziel=st.dest_root)
        self._checkpoint(force=True)
        os.makedirs(long_path(self.guard.check(st.dest_root)), exist_ok=True)

        total_bytes = int(st.stats.get("bytes", 0)) or 1
        done_bytes = 0
        copied = skipped = 0
        stack = [""]
        while stack:
            self._check_cancel()
            rel_dir = stack.pop()
            src_dir = os.path.join(st.source_root, *rel_dir.split("/")) if rel_dir else st.source_root
            try:
                with os.scandir(long_path(src_dir)) as it:
                    items = sorted(it, key=lambda d: d.name.casefold())
            except OSError as exc:
                st.copy_errors.append({"pfad": rel_dir or ".", "fehler": explain_os_error(exc)})
                continue
            subdirs = []
            for d in items:
                rel = f"{rel_dir}/{d.name}" if rel_dir else d.name
                try:
                    if d.is_symlink() or (getattr(d, "is_junction", None) and d.is_junction()):
                        continue
                    if d.is_dir(follow_symlinks=False):
                        dst = self._dest(rel)
                        os.makedirs(long_path(dst), exist_ok=True)
                        _copy_win_attributes(d.path, dst)  # z. B. versteckte Ordner bleiben versteckt
                        subdirs.append(rel)
                        continue
                    src_st = d.stat(follow_symlinks=False)
                except OSError as exc:
                    st.copy_errors.append({"pfad": rel, "fehler": explain_os_error(exc)})
                    continue
                self._check_cancel()
                dst = self._dest(rel)
                if _same_file(src_st, dst):
                    skipped += 1
                    done_bytes += src_st.st_size
                    continue
                try:
                    done_bytes = self._copy_file(d.path, dst, rel, done_bytes, total_bytes)
                    copied += 1
                except ExecutionCancelled:
                    raise
                except OSError as exc:
                    msg = explain_os_error(exc)
                    st.copy_errors.append({"pfad": rel, "fehler": msg})
                    st.log("kopierfehler", pfad=rel, fehler=msg)
                self._checkpoint()
            stack.extend(reversed(subdirs))
        st.stats.update({"kopiert": copied, "bereits_vorhanden": skipped, "kopierfehler": len(st.copy_errors)})
        st.phase = PH_COPIED
        st.log("kopieren_beendet", kopiert=copied, uebersprungen=skipped, fehler=len(st.copy_errors))
        self._checkpoint(force=True)
        return st.copy_errors

    def retry_copy(self) -> list[dict]:
        """Versucht fehlgeschlagene Kopien erneut (bereits kopierte Dateien werden übersprungen)."""
        if self.state.phase == PH_COPIED:
            self.state.phase = PH_COPYING
        return self.copy_phase()

    def _copy_file(self, src: str, dst: str, rel: str, done: int, total: int) -> int:
        part = self.guard.check(dst + PART_SUFFIX)
        try:
            with open(long_path(src), "rb") as fin, open(long_path(part), "wb") as fout:
                while True:
                    if self.cancel.is_set():
                        raise ExecutionCancelled()
                    buf = fin.read(CHUNK)
                    if not buf:
                        break
                    fout.write(buf)
                    done += len(buf)
                    self.progress(Progress("copy", min(done, total), total, rel))
            try:
                shutil.copystat(long_path(src), long_path(part))
            except OSError:
                pass  # z. B. Dateisysteme ohne Zeitstempel-/Rechteunterstützung
            _copy_win_attributes(src, part)
            if os.path.exists(long_path(dst)):
                # Nur eigene, unvollständige Kopien aus einem früheren Versuch werden ersetzt.
                os.chmod(long_path(dst), 0o666)
            os.replace(long_path(part), long_path(dst))
        except BaseException:
            try:
                if os.path.exists(long_path(part)):
                    os.chmod(long_path(part), 0o666)
                    os.remove(long_path(part))
            except OSError:
                pass
            if self.cancel.is_set():
                self.state.save()
                raise ExecutionCancelled()
            raise
        self.progress(Progress("copy", min(done, total), total, rel))
        return done

    # -- Phase 2: Änderungen in der Kopie umsetzen ----------------------------
    def _walk_copy_files(self) -> list[str]:
        out = []
        stack = [""]
        while stack:
            rel_dir = stack.pop()
            base = self._dest(rel_dir)
            with os.scandir(long_path(base)) as it:
                for d in it:
                    rel = f"{rel_dir}/{d.name}" if rel_dir else d.name
                    if rel_dir == "" and d.name == STAGING_DIR:
                        continue
                    if d.name.endswith(PART_SUFFIX):
                        continue
                    if d.is_symlink():
                        continue
                    if d.is_dir(follow_symlinks=False):
                        stack.append(rel)
                    else:
                        out.append(rel)
        return sorted(out, key=str.casefold)

    def apply_phase(self) -> None:
        st = self.state
        if st.phase == PH_DONE:
            return
        if st.phase not in (PH_COPIED, PH_APPLYING):
            raise RuntimeError(f"Änderungen können in Phase „{st.phase}“ nicht umgesetzt werden.")
        mapper = st.get_mapper()
        if st.phase == PH_COPIED:
            moves = []
            for i, rel in enumerate(self._walk_copy_files()):
                final = mapper.resolve(rel)
                if final != rel:
                    moves.append(asdict(MoveRecord(src=rel, dst=final, stage=f"{STAGING_DIR}/{i}",
                                                   op_id=mapper.responsible_op(rel))))
            st.moves = moves
            st.phase = PH_APPLYING
            st.log("umsetzen_gestartet", dateien=len(moves))
            self._checkpoint(force=True)

        self._relocate(st.moves, "apply")
        # Leere Zielordner (z. B. umbenannte leere Ordner, neue Ordner) anlegen
        for d in st.final_dirs:
            self._check_cancel()
            try:
                self._makedirs(d)
            except OSError as exc:
                st.apply_errors.append({"pfad": d, "fehler": explain_os_error(exc)})
        self._fix_case(st.final_dirs)
        self._remove_vacated(st.vacated_dirs, keep=st.final_dirs, record=st.removed_dirs)
        self._cleanup_staging()

        ok = sum(1 for m in st.moves if m["status"] == "done")
        failed = [m for m in st.moves if m["status"] == "failed"]
        st.stats.update({"verschoben": ok, "fehlgeschlagen": len(failed)})
        st.phase = PH_DONE
        st.log("umsetzen_beendet", erfolgreich=ok, fehlgeschlagen=len(failed))
        self._checkpoint(force=True)
        write_report(st)

    def _reconcile(self, moves: list[dict]) -> None:
        """Gleicht das Journal nach einer Unterbrechung mit dem Dateisystem ab."""
        for m in moves:
            if m["status"] in ("done", "failed"):
                continue
            src_e = os.path.lexists(long_path(self._dest(m["src"])))
            stage_e = os.path.lexists(long_path(self._dest(m["stage"])))
            dst_e = os.path.lexists(long_path(self._dest(m.get("final") or m["dst"])))
            if m["status"] == "staged" and not stage_e and dst_e:
                m["status"] = "done"
                m["final"] = m.get("final") or m["dst"]
            elif m["status"] == "pending" and not src_e:
                if stage_e:
                    m["status"] = "staged"
                elif dst_e:
                    m["status"] = "done"
                    m["final"] = m.get("final") or m["dst"]

    def _relocate(self, moves: list[dict], kind: str) -> None:
        """Verschiebt Dateien zweistufig über einen Zwischenordner (kein Überschreiben möglich)."""
        st = self.state
        self._reconcile(moves)
        total = len(moves)
        label = "apply" if kind == "apply" else "undo"
        staging_needed = any(m["status"] == "pending" for m in moves)
        if staging_needed:
            os.makedirs(long_path(self._dest(STAGING_DIR)), exist_ok=True)
        # Stufe 1: in den Zwischenordner
        for i, m in enumerate(moves):
            if m["status"] != "pending":
                continue
            self._check_cancel()
            try:
                os.rename(long_path(self._dest(m["src"])), long_path(self._dest(m["stage"])))
                m["status"] = "staged"
            except OSError as exc:
                m["status"] = "failed"
                m["error"] = explain_os_error(exc)
                st.apply_errors.append({"pfad": m["src"], "fehler": m["error"]})
                st.log("fehlgeschlagen", aktion=kind, quelle=m["src"], ziel=m["dst"], fehler=m["error"], op=m.get("op_id"))
            self.progress(Progress(label, i + 1, total * 2, m["src"], "Vorbereiten"))
            self._checkpoint()
        # Stufe 2: an den endgültigen Ort
        for i, m in enumerate(moves):
            if m["status"] != "staged":
                continue
            self._check_cancel()
            target = m["dst"]
            try:
                self._makedirs(parent_of(target))
                if os.path.lexists(long_path(self._dest(target))):
                    target = self._free_name(target)
                    st.notes.append(f"„{m['dst']}“ war bereits vorhanden – abgelegt als „{target}“.")
                os.rename(long_path(self._dest(m["stage"])), long_path(self._dest(target)))
                m["status"] = "done"
                m["final"] = target
                st.log("ausgefuehrt", aktion=kind, quelle=m["src"], ziel=target, op=m.get("op_id"))
            except OSError as exc:
                # Datei bleibt im Zwischenordner erhalten – nichts geht verloren.
                m["error"] = explain_os_error(exc)
                st.apply_errors.append({"pfad": m["src"], "fehler": m["error"]})
                st.log("fehlgeschlagen", aktion=kind, quelle=m["src"], ziel=target, fehler=m["error"], op=m.get("op_id"))
                try:
                    back = self._free_name(m["src"])
                    self._makedirs(parent_of(back))
                    os.rename(long_path(self._dest(m["stage"])), long_path(self._dest(back)))
                    m["status"] = "failed"
                    if back != m["src"]:
                        st.notes.append(f"„{m['src']}“ wurde als „{back}“ abgelegt.")
                except OSError:
                    m["status"] = "failed"
                    st.notes.append(f"„{m['src']}“ liegt noch im Zwischenordner {STAGING_DIR}.")
            self.progress(Progress(label, total + i + 1, total * 2, target, "Ablegen"))
            self._checkpoint()
        self._checkpoint(force=True)

    def _free_name(self, rel: str) -> str:
        """``rel`` selbst, falls frei – sonst „Name (2).ext“, „Name (3).ext“ …"""
        if not os.path.lexists(long_path(self._dest(rel))):
            return rel
        parent, name = parent_of(rel), rel.rpartition("/")[2]
        stem, ext = split_ext(name)
        n = 2
        while True:
            cand = join_rel(parent, f"{stem} ({n}){ext}")
            if not os.path.lexists(long_path(self._dest(cand))):
                return cand
            n += 1

    def _fix_case(self, dirs: list[str]) -> None:
        """Korrigiert reine Groß-/Kleinschreibungsänderungen von Ordnern (Windows)."""
        for d in sorted(dirs, key=lambda p: p.count("/")):
            parent, name = parent_of(d), d.rpartition("/")[2]
            parent_path = self._dest(parent)
            try:
                existing = [n for n in os.listdir(long_path(parent_path)) if n.casefold() == name.casefold()]
            except OSError:
                continue
            if len(existing) == 1 and existing[0] != name:
                old_rel = join_rel(parent, existing[0])
                tmp_rel = join_rel(parent, f"{name}.kiorg-tmp")
                try:
                    os.rename(long_path(self._dest(old_rel)), long_path(self._dest(tmp_rel)))
                    os.rename(long_path(self._dest(tmp_rel)), long_path(self._dest(d)))
                    self.state.case_renames.append([old_rel, d])
                except OSError as exc:
                    self.state.apply_errors.append({"pfad": d, "fehler": explain_os_error(exc)})

    def _remove_vacated(self, candidates: list[str], keep: list[str], record: list[str]) -> None:
        """Entfernt ausschließlich *leere* Ordner, die durch Verschieben frei wurden."""
        keep_keys: set[str] = set()
        for k in keep:
            parts = path_key(k).split("/")
            for i in range(1, len(parts) + 1):
                keep_keys.add("/".join(parts[:i]))
        for rel in sorted(set(candidates), key=lambda p: -p.count("/")):
            # auch leere Elternordner des freigewordenen Ordners berücksichtigen
            cur = rel
            while cur:
                if path_key(cur) in keep_keys:
                    break
                path = self._dest(cur)
                try:
                    if os.path.isdir(long_path(path)) and not os.listdir(long_path(path)):
                        os.rmdir(long_path(path))
                        record.append(cur)
                        self.state.log("leeren_ordner_entfernt", pfad=cur)
                    else:
                        break
                except OSError:
                    break
                cur = parent_of(cur)

    def _cleanup_staging(self) -> None:
        path = self._dest(STAGING_DIR)
        try:
            if os.path.isdir(long_path(path)) and not os.listdir(long_path(path)):
                os.rmdir(long_path(path))
        except OSError:
            pass

    def run_all(self) -> RunState:
        """Kopiert (falls nötig) und setzt danach alle Änderungen um."""
        if self.state.phase in (PH_CREATED, PH_COPYING):
            self.copy_phase()
        if self.state.phase in (PH_COPIED, PH_APPLYING):
            self.apply_phase()
        if self.state.phase == PH_UNDOING:
            self.undo()
        return self.state

    # -- Rückgängig ---------------------------------------------------------
    def undo(self) -> None:
        """Stellt in der Kopie die ursprüngliche Struktur wieder her."""
        st = self.state
        if st.phase not in (PH_DONE, PH_APPLYING, PH_UNDOING):
            raise RuntimeError("Für diesen Vorgang gibt es nichts rückgängig zu machen.")
        if st.phase != PH_UNDOING:
            st.undo_moves = [
                asdict(MoveRecord(src=m["final"], dst=m["src"], stage=f"{STAGING_DIR}/u{i}", op_id=m.get("op_id")))
                for i, m in enumerate(st.moves)
                if m["status"] == "done"
            ]
            st.phase = PH_UNDOING
            st.log("rueckgaengig_gestartet", dateien=len(st.undo_moves))
            self._checkpoint(force=True)
        # Ordner zurückbenennen, deren Schreibweise geändert wurde
        for old_rel, new_rel in reversed(st.case_renames):
            try:
                if os.path.isdir(long_path(self._dest(new_rel))):
                    tmp = new_rel + ".kiorg-tmp"
                    os.rename(long_path(self._dest(new_rel)), long_path(self._dest(tmp)))
                    os.rename(long_path(self._dest(tmp)), long_path(self._dest(old_rel)))
            except OSError:
                pass
        st.case_renames = []
        for rel in reversed(st.removed_dirs):
            os.makedirs(long_path(self._dest(rel)), exist_ok=True)
        self._relocate(st.undo_moves, "undo")
        # Vom Organisator angelegte Ordner wieder entfernen, sofern leer
        removed: list[str] = []
        self._remove_vacated(st.created_dirs + st.final_dirs, keep=st.original_dirs, record=removed)
        self._cleanup_staging()
        st.phase = PH_UNDONE
        st.log("rueckgaengig_beendet", zurueck=sum(1 for m in st.undo_moves if m["status"] == "done"))
        self._checkpoint(force=True)
        write_report(st)



# ---------------------------------------------------------------------------
# Änderungsprotokoll
# ---------------------------------------------------------------------------


def write_report(state: RunState) -> Path:
    """Schreibt ein gut lesbares Änderungsprotokoll (Textdatei)."""
    lines = [
        "KI-Ordnerorganisator – Änderungsprotokoll",
        "=" * 44,
        f"Vorgang:   {state.run_id}",
        f"Erstellt:  {state.created_at}",
        f"Status:    {PHASE_LABELS_DE.get(state.phase, state.phase)}",
        f"Original:  {state.source_root}  (unverändert)",
        f"Kopie:     {state.dest_root}",
        "",
    ]
    ops = state.plan.get("operations", [])
    status_by_op: dict[str, list[str]] = {}
    for m in state.moves:
        if m.get("op_id"):
            status_by_op.setdefault(m["op_id"], []).append(m["status"])
    lines.append(f"Genehmigte Änderungen ({len(ops)}):")
    for op in ops:
        sts = status_by_op.get(op["id"], [])
        if not sts:
            mark = "ausgeführt" if op["type"] == "create_folder" or state.phase in (PH_DONE, PH_UNDONE) else "offen"
        elif all(s == "done" for s in sts):
            mark = "ausgeführt"
        elif any(s == "done" for s in sts):
            mark = "teilweise ausgeführt"
        elif any(s == "failed" for s in sts):
            mark = "FEHLGESCHLAGEN"
        else:
            mark = "offen"
        src = f"{op.get('source')} → " if op.get("source") else ""
        lines.append(f"  [{mark}] {op['id']} {op['type']}: {src}{op['target']}")
        if op.get("reason"):
            lines.append(f"           Begründung: {op['reason']}")
    if state.rejected:
        lines.append("")
        lines.append(f"Abgelehnte Vorschläge ({len(state.rejected)}) – nicht umgesetzt:")
        for op in state.rejected:
            src = f"{op.get('source')} → " if op.get("source") else ""
            lines.append(f"  {op['id']} {op['type']}: {src}{op['target']}")
    if state.copy_errors:
        lines.append("")
        lines.append(f"Kopierfehler ({len(state.copy_errors)}):")
        lines.extend(f"  {e['pfad']}: {e['fehler']}" for e in state.copy_errors)
    if state.apply_errors:
        lines.append("")
        lines.append(f"Fehlgeschlagene Aktionen ({len(state.apply_errors)}):")
        lines.extend(f"  {e['pfad']}: {e['fehler']}" for e in state.apply_errors)
    if state.notes:
        lines.append("")
        lines.append("Hinweise:")
        lines.extend(f"  {n}" for n in state.notes)
    done_moves = [m for m in state.moves if m["status"] == "done"]
    if done_moves:
        lines.append("")
        lines.append(f"Verschobene/umbenannte Dateien in der Kopie ({len(done_moves)}):")
        lines.extend(f"  {m['src']} → {m['final'] or m['dst']}" for m in done_moves)
    if state.phase == PH_UNDONE:
        lines.append("")
        lines.append("Alle Änderungen in der Kopie wurden rückgängig gemacht.")
    state.run_dir.mkdir(parents=True, exist_ok=True)
    state.report_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return state.report_file
