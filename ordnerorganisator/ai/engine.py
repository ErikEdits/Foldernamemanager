"""Die integrierte Organisations-KI (kostenlos, offline, ohne externe Abhängigkeiten).

Sie kombiniert Erkennungs- und Regellogik, die speziell auf Ordnerorganisation
ausgerichtet ist:

* Projektart erkennen (allgemein, Java, Minecraft-Plugin, Video, Fotos),
* Namen bereinigen und vereinheitlichen (Schreibweise, Datum, Sprache),
* Dateien nach Typ gruppieren – vorhandene Sammelordner werden bevorzugt,
* Spezialstrukturen für Video- und Fotoprojekte,
* Software-Projekte und Systemordner schützen,
* bei Unsicherheit nachfragen statt raten.

Jeder Vorschlag enthält eine Begründung und eine Sicherheit (0–1).
Vorschläge mit geringer Sicherheit (< 0,5) werden nicht vorausgewählt.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field, replace

from ..content import ContentInfo
from ..filetypes import MEDIA_KEYS, NO_RENAME_KEYS, category_by_key, category_for_folder_name
from ..models import CONVENTIONAL_ROOT_STEMS, Entry, Snapshot
from ..plan import CREATE_FOLDER, MOVE, RENAME, Operation, Plan
from ..planner import ERROR, resolve_plan
from ..settings import UserRules
from ..windows_names import join_rel, name_of, parent_of, path_key, split_ext
from .base import AnalysisCancelled, AnalysisRequest, ProgressFn, Provider
from .naming import detect_language, extract_date, is_generic_name, style_name, suggest_name, title_to_filename
from .projecttypes import (
    JAVA_BUILD_FILES,
    MINECRAFT_MARKERS,
    PROJECT_LABELS_DE,
    Detection,
    detect_project_type,
    is_code_project_dir,
    is_video_project_dir,
)

ACCEPT_THRESHOLD = 0.5

SIDECAR_EXTS = frozenset({".xmp", ".aae", ".thm", ".srt", ".vtt", ".lrv"})

PHOTO_DUMP_NAMES = frozenset(
    "unsortiert unsorted import imports neu new kamera camera dcim handy phone upload uploads "
    "downloads eingang inbox camera_roll kamerarolle 100apple 100media 100andro".split()
)

JAVA_PROTECTED_DIRS = frozenset(
    "src build target out bin gradle .gradle .mvn .idea .settings run server libs lib docs".split()
)

VIDEO_SLOTS = (
    # Schlüssel, Name DE, Name EN, Kategorien, bekannte Ordnernamen
    ("project", "01_Projektdateien", "01_Project_Files", {"video_projects"},
     {"projekt", "projekte", "project", "projects", "projektdateien", "projectfiles"}),
    ("footage", "02_Videomaterial", "02_Footage", {"videos"},
     {"footage", "rohmaterial", "videomaterial", "clips", "material", "raw", "videos", "video", "aufnahmen"}),
    ("audio", "03_Audio", "03_Audio", {"audio"},
     {"audio", "musik", "music", "sound", "sounds", "sfx", "ton", "töne", "toene"}),
    ("graphics", "04_Grafiken", "04_Graphics", {"images"},
     {"grafiken", "graphics", "bilder", "images", "titel", "titles", "logos", "overlays"}),
    ("export", "05_Export", "05_Exports", set(),
     {"export", "exports", "render", "renders", "output", "ausgabe", "final", "fertig"}),
)
_EXPORT_HINTS = ("export", "final", "render", "master", "fertig", "output", "upload")


def resolve_language(rules: UserRules, snapshot: Snapshot) -> tuple[str, bool]:
    """Liefert (Sprache, sicher erkannt?)."""
    if rules.language in ("de", "en"):
        return rules.language, True
    names = [e.name for e in snapshot.root.iter_tree() if not e.is_special]
    detected = detect_language(names)
    return (detected or "de"), detected is not None


def _alnum(name: str) -> str:
    return "".join(c for c in name.casefold() if c.isalnum())


def _slot_key(name: str) -> str:
    return _alnum(name.lstrip("0123456789_- "))


@dataclass
class _FilePlan:
    new_dir: str | None = None  # Zielordner (neue Struktur) – None = bleibt im (ggf. umbenannten) Ordner
    new_name: str | None = None
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.85


class BuiltinEngine(Provider):
    id = "builtin"
    name = "Integrierte KI (offline, kostenlos)"
    is_external = False

    def analyze(self, request: AnalysisRequest, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> Plan:
        excluded: set[str] = set()
        for _ in range(5):
            run = _EngineRun(request, excluded, progress, cancel)
            plan, bad_dir_renames = run.run()
            if not bad_dir_renames:
                return plan
            excluded |= bad_dir_renames
        return plan


class _EngineRun:
    def __init__(self, req: AnalysisRequest, excluded_dir_renames: set[str],
                 progress: ProgressFn | None, cancel: threading.Event | None):
        self.req = req
        self.snap: Snapshot = req.snapshot
        self.rules: UserRules = req.rules
        self.contents: dict[str, ContentInfo] = req.contents or {}
        self.progress = progress or (lambda msg: None)
        self.cancel = cancel
        self.lang, self.lang_sure = resolve_language(self.rules, self.snap)
        if self.rules.project_mode == "auto":
            self.detection = detect_project_type(self.snap)
        else:
            self.detection = Detection(self.rules.project_mode, 1.0, ["vom Benutzer gewählt"])
        self.mode = self.detection.mode
        self.excluded_dir_renames = excluded_dir_renames

        self.protected: set[str] = set()  # Schlüssel von Ordnern, deren Inhalt unberührt bleibt
        self.handled_dirs: set[str] = set()  # Ordner, die ein Spezialmodus bereits behandelt
        self.dir_final: dict[str, str] = {"": ""}
        self.dir_ops: dict[str, tuple[str, list[str], float]] = {}
        self.file_plans: dict[str, _FilePlan] = {}
        self.create_ops: list[tuple[str, str, float]] = []
        self.new_dirs: dict[str, str] = {}  # Schlüssel -> Pfad neu anzulegender Ordner
        self.questions: list[str] = []
        self.warnings: list[str] = []
        self.generic_files: list[str] = []
        self.generic_dirs: list[str] = []
        self.ambiguous_dates: list[str] = []
        self.protected_projects: list[str] = []

    # ------------------------------------------------------------------
    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise AnalysisCancelled()

    def is_protected(self, rel: str) -> bool:
        if self.snap.is_protected(rel):
            return True
        parts = rel.split("/") if rel else []
        for i in range(len(parts), -1, -1):
            if path_key("/".join(parts[:i])) in self.protected:
                return True
        return False

    def fp(self, e: Entry) -> _FilePlan:
        return self.file_plans.setdefault(e.key, _FilePlan())

    def folder_name(self, base: str) -> str:
        style = self.rules.naming_style if self.rules.naming_style != "keep" else "title_underscore"
        return style_name(base, style)

    def category_folder(self, cat_key: str) -> str:
        preferred = self.rules.preferred_folder_names.get(cat_key)
        if preferred:
            return preferred
        return self.folder_name(category_by_key(cat_key).folder(self.lang))

    # ------------------------------------------------------------------
    def run(self) -> tuple[Plan, set[str]]:
        self.progress("Struktur wird untersucht …")
        self._mark_protected()
        self._check_cancel()
        if self.mode == "video":
            self._video_mode()
        elif self.mode == "photos":
            self._photo_mode()
        elif self.mode in ("java", "minecraft"):
            self._java_mode()
        self._check_cancel()
        self.progress("Ordnernamen werden geprüft …")
        if self.rules.rename_folders and self.mode not in ("java", "minecraft"):
            self._folder_renames()
        self._check_cancel()
        if self.rules.group_by_type and self.mode in ("general", "photos"):
            self.progress("Dateien werden gruppiert …")
            for d in [self.snap.root] + self.snap.dirs():
                if d.is_dir and not d.is_link:
                    self._group_folder(d)
        self._check_cancel()
        if self.rules.rename_files and self.mode not in ("java", "minecraft"):
            self.progress("Dateinamen werden geprüft …")
            self._file_renames()
        self._sidecars()
        self._check_cancel()
        return self._finalize()

    # ------------------------------------------------------------------
    def _mark_protected(self) -> None:
        root = self.snap.root
        for d in [root] + self.snap.dirs():
            if d.is_link:
                continue
            if d.rel_path and (d.is_special or d.name.startswith(".")):
                self.protected.add(d.key)
                continue
            if self.mode in ("java", "minecraft") and d.depth == 1 and d.name.casefold() in JAVA_PROTECTED_DIRS:
                self.protected.add(d.key)
                continue
            if d.rel_path == "" and self.mode in ("java", "minecraft"):
                continue
            if is_code_project_dir(d):
                self.protected.add(d.key)
                self.protected_projects.append(d.rel_path or ".")
            elif self.mode != "video" and d.rel_path and is_video_project_dir(d):
                self.protected.add(d.key)
                self.protected_projects.append(d.rel_path)
        if "" in self.protected:
            self.warnings.append(
                "Der gewählte Ordner ist ein Software-Projekt. Seine Struktur wird nicht verändert, "
                "da Build-Werkzeuge feste Pfade erwarten."
            )
        elif self.protected_projects:
            shown = ", ".join(f"„{p}“" for p in self.protected_projects[:5])
            more = f" und {len(self.protected_projects) - 5} weitere" if len(self.protected_projects) > 5 else ""
            self.warnings.append(
                f"Projektordner bleiben unverändert (Software- oder Videoschnittprojekte): {shown}{more}."
            )

    def _loose_files(self, d: Entry) -> list[Entry]:
        out = []
        for c in d.children:
            if c.is_dir or c.is_link or c.is_special:
                continue
            if d.rel_path == "" and split_ext(c.name)[0].casefold() in CONVENTIONAL_ROOT_STEMS:
                continue
            out.append(c)
        return out

    # -- Ordner umbenennen ----------------------------------------------
    def _folder_renames(self) -> None:
        translate = self.rules.translate_existing and (self.lang_sure or self.rules.language != "auto")
        dirs = sorted(self.snap.dirs(), key=lambda e: e.depth)
        for d in dirs:
            if d.is_link or self.is_protected(d.rel_path) or d.key in self.handled_dirs:
                if d.key not in self.dir_final:
                    self.dir_final[d.key] = join_rel(self.dir_final.get(path_key(d.parent_rel), d.parent_rel), d.name)
                continue
            parent_final = self.dir_final.get(path_key(d.parent_rel), d.parent_rel)
            if d.key in self.dir_ops:  # z. B. durch Spezialmodus festgelegt
                self.dir_final[d.key] = join_rel(parent_final, self.dir_ops[d.key][0])
                continue
            new_name = d.name
            if is_generic_name(d.name, is_dir=True):
                self.generic_dirs.append(d.rel_path)
            elif d.key not in self.excluded_dir_renames:
                change = suggest_name(d.name, is_dir=True, rules=self.rules, lang=self.lang, translate=translate)
                if change and self._sibling_free(d, change.new_name):
                    new_name = change.new_name
                    self.dir_ops[d.key] = (new_name, change.reasons, change.confidence)
            self.dir_final[d.key] = join_rel(parent_final, new_name)

    def _sibling_free(self, d: Entry, new_name: str) -> bool:
        """Prüft, ob ein neuer Ordnername mit Geschwistern kollidiert."""
        parent = self.snap.entries[path_key(d.parent_rel)]
        wanted = new_name.casefold()
        if wanted == d.name.casefold():
            return True  # nur Schreibweise geändert
        for sib in parent.children:
            if sib is d:
                continue
            current = self.dir_ops.get(sib.key, (sib.name,))[0] if sib.is_dir else sib.name
            if current.casefold() == wanted or sib.name.casefold() == wanted:
                return False
        return True

    def final_dir(self, d: Entry) -> str:
        if d.key in self.dir_final:
            return self.dir_final[d.key]
        parent = self.snap.entries.get(path_key(d.parent_rel))
        base = self.final_dir(parent) if parent is not None and d.rel_path else ""
        return join_rel(base, d.name) if d.rel_path else ""

    # -- Gruppieren nach Dateityp -----------------------------------------
    def _group_folder(self, d: Entry) -> None:
        if self.is_protected(d.rel_path) or d.key in self.handled_dirs:
            return
        if d.rel_path and category_for_folder_name(d.name) is not None:
            return  # Ordner ist selbst ein Sammelordner (z. B. „Bilder“)
        loose = [f for f in self._loose_files(d) if self.fp(f).new_dir is None]
        if self.mode == "photos":
            loose = [f for f in loose if f.category not in MEDIA_KEYS]
        if not loose:
            return
        existing: dict[str, Entry] = {}
        for c in d.children:
            if c.is_dir and not c.is_link and not c.is_special:
                cat = category_for_folder_name(c.name)
                if cat is not None and cat.key not in existing:
                    existing[cat.key] = c
                for key, pref in self.rules.preferred_folder_names.items():
                    if pref and pref.casefold() == c.name.casefold():
                        existing.setdefault(key, c)
        cats = Counter(f.category for f in loose if f.category != "other")
        if not cats:
            return
        groups = {("media" if k in MEDIA_KEYS else k) for k in cats}
        dominant = None
        for k, n in cats.items():
            if n / len(loose) >= 0.7:
                dominant = k
        mixed = len(loose) >= self.rules.min_files_for_grouping and len(groups) >= 2
        if not existing and not mixed:
            return
        d_final = self.final_dir(d)
        child_names = {c.name.casefold() for c in d.children}
        for f in loose:
            cat = f.category
            if cat == "other":
                continue
            if cat in existing:
                target = self.final_dir(existing[cat])
                conf = 0.75
                reason = f"{category_by_key(cat).label('de')} in den vorhandenen Ordner „{existing[cat].name}“"
            else:
                if not existing:
                    if dominant and (cat == dominant or (dominant in MEDIA_KEYS and cat in MEDIA_KEYS)):
                        continue
                    if dominant and cats[cat] < 2:
                        continue
                folder = self.category_folder(cat)
                if folder.casefold() in child_names and not any(
                    c.is_dir and c.name.casefold() == folder.casefold() for c in d.children
                ):
                    continue  # Name ist bereits durch eine Datei belegt
                target = join_rel(d_final, folder)
                conf = 0.7
                reason = f"{category_by_key(cat).label('de')}-Dateien im Ordner „{folder}“ sammeln"
            plan = self.fp(f)
            plan.new_dir = target
            plan.reasons.append(reason)
            plan.confidence = min(plan.confidence, conf)

    # -- Dateien umbenennen -----------------------------------------------
    def _file_renames(self) -> None:
        for f in self.snap.files():
            if f.is_link or f.is_special or self.is_protected(f.rel_path):
                continue
            if f.category in NO_RENAME_KEYS:
                continue
            if self.mode == "video" and f.category in MEDIA_KEYS | {"audio", "video_projects"}:
                continue
            if f.depth == 1 and split_ext(f.name)[0].casefold() in CONVENTIONAL_ROOT_STEMS:
                continue
            if is_generic_name(f.name):
                info = self.contents.get(f.rel_path)
                if info is not None and info.title:
                    new_name = title_to_filename(info.title, f.ext, self.rules)
                    if new_name and new_name.casefold() != f.name.casefold():
                        plan = self.fp(f)
                        plan.new_name = new_name
                        plan.reasons.append(f"Name aus dem Inhalt abgeleitet (Überschrift/Titel: „{info.title}“)")
                        plan.confidence = min(plan.confidence, 0.5)
                        continue
                self.generic_files.append(f.rel_path)
                # Nur die Dateiendung angleichen, den Namen nicht „raten“
                if self.rules.lowercase_extensions and f.ext and split_ext(f.name)[1] != f.ext:
                    plan = self.fp(f)
                    plan.new_name = split_ext(f.name)[0] + f.ext
                    plan.reasons.append("Dateiendung kleingeschrieben")
                continue
            change = suggest_name(f.name, is_dir=False, rules=self.rules, lang=self.lang)
            if change is not None and change.removed_copy_marker and self._name_taken(f, change.new_name):
                # Das „Original“ existiert noch – Kopie-Kennzeichnung bleibt erhalten.
                change = suggest_name(
                    f.name, is_dir=False, rules=replace(self.rules, remove_copy_markers=False), lang=self.lang
                )
            if change is None:
                continue
            self.ambiguous_dates.extend(f"{f.rel_path} ({a})" for a in change.ambiguous_dates)
            plan = self.fp(f)
            plan.new_name = change.new_name
            plan.reasons.extend(change.reasons)
            plan.confidence = min(plan.confidence, change.confidence)

    def _name_taken(self, f: Entry, new_name: str) -> bool:
        parent = self.snap.entries[path_key(f.parent_rel)]
        wanted = new_name.casefold()
        return any(c is not f and c.name.casefold() == wanted for c in parent.children)

    def _sidecars(self) -> None:
        """Begleitdateien (z. B. .xmp, .srt) folgen ihrer Hauptdatei."""
        moved: dict[tuple[str, str], str] = {}
        for key, plan in self.file_plans.items():
            e = self.snap.entries[key]
            if plan.new_dir is not None and e.ext not in SIDECAR_EXTS:
                moved[(path_key(e.parent_rel), split_ext(e.name)[0].casefold())] = plan.new_dir
        if not moved:
            return
        for f in self.snap.files():
            if f.ext not in SIDECAR_EXTS or self.is_protected(f.rel_path):
                continue
            stem = split_ext(f.name)[0].casefold()
            target = moved.get((path_key(f.parent_rel), stem)) or moved.get(
                (path_key(f.parent_rel), split_ext(stem)[0])
            )
            if target and self.fp(f).new_dir is None:
                plan = self.fp(f)
                plan.new_dir = target
                plan.reasons.append("Begleitdatei folgt der zugehörigen Datei")
                plan.confidence = min(plan.confidence, 0.7)

    # -- Spezialmodus: Video ---------------------------------------------
    def _video_mode(self) -> None:
        root = self.snap.root
        slot_final: dict[str, str] = {}
        for slot, name_de, name_en, _cats, aliases in VIDEO_SLOTS:
            standard = self.folder_name(name_en if self.lang == "en" else name_de)
            match = None
            for c in root.children:
                if c.is_dir and not c.is_link and not self.is_protected(c.rel_path) and c.key not in self.handled_dirs:
                    if _slot_key(c.name) in {_alnum(a) for a in aliases} or _slot_key(c.name) == _slot_key(standard):
                        match = c
                        break
            if match is not None:
                self.handled_dirs.add(match.key)
                if match.name != standard and self._sibling_free(match, standard) and match.key not in self.excluded_dir_renames:
                    self.dir_ops[match.key] = (standard, ["Standardstruktur für Videoprojekte (nummeriert)"], 0.6)
                    slot_final[slot] = standard
                else:
                    slot_final[slot] = match.name
                self.dir_final[match.key] = slot_final[slot]
            else:
                slot_final[slot] = standard
        used: set[str] = set()
        for f in self._loose_files(root):
            slot = None
            conf = 0.65
            reason = ""
            if f.category == "video_projects":
                slot, reason = "project", "Schnittprojekt-Datei"
            elif f.category == "videos":
                if any(h in f.name.casefold() for h in _EXPORT_HINTS):
                    slot, conf, reason = "export", 0.5, "Name deutet auf einen fertigen Export hin"
                else:
                    slot, reason = "footage", "Videomaterial"
            elif f.category == "audio":
                slot, reason = "audio", "Audiodatei"
            elif f.category == "images":
                slot, reason = "graphics", "Grafik/Bild für das Video"
            if slot is None:
                continue
            plan = self.fp(f)
            plan.new_dir = slot_final[slot]
            plan.reasons.append(f"{reason} → „{slot_final[slot]}“")
            plan.confidence = min(plan.confidence, conf)
            used.add(slot)
        existing_names = {c.name.casefold() for c in root.children}
        for slot, *_ in VIDEO_SLOTS:
            if slot not in used and slot_final[slot].casefold() not in existing_names:
                self.create_ops.append((slot_final[slot], "Leerer Standardordner der Videoprojekt-Struktur", 0.45))
        self.warnings.append(
            "Videoschnittprogramme speichern Pfade zu Mediendateien. Nach dem Verschieben müssen Medien "
            "im Projekt der Kopie ggf. neu verknüpft werden (das Original bleibt unverändert)."
        )

    # -- Spezialmodus: Fotos ---------------------------------------------
    def _photo_mode(self) -> None:
        root = self.snap.root
        sources: list[Entry] = list(self._loose_files(root))
        for c in root.children:
            if c.is_dir and not self.is_protected(c.rel_path) and _alnum(c.name) in {_alnum(n) for n in PHOTO_DUMP_NAMES}:
                sources.extend(self._loose_files(c))
        undated = 0
        for f in sources:
            if f.category not in MEDIA_KEYS:
                continue
            d = extract_date(f.name)
            if d is None:
                undated += 1
                continue
            plan = self.fp(f)
            plan.new_dir = f"{d.year}/{d.year}-{d.month:02d}"
            plan.reasons.append(f"Aufnahmedatum laut Dateiname: {d.isoformat()} → Ordner nach Jahr/Monat")
            plan.confidence = min(plan.confidence, 0.75)
        if undated:
            self.questions.append(
                f"{undated} Foto(s)/Video(s) enthalten kein Datum im Namen. Ohne Inhaltsanalyse "
                "(z. B. Aufnahmedatum) bleiben sie an ihrem Ort."
            )
        # Jahresordner werden nicht umbenannt
        for c in root.children:
            if c.is_dir and c.name.isdigit() and len(c.name) == 4:
                self.handled_dirs.add(c.key)

    # -- Spezialmodus: Java / Minecraft -----------------------------------
    def _java_mode(self) -> None:
        root = self.snap.root
        root_names = {c.name.casefold() for c in root.children}
        has_build = bool(root_names & JAVA_BUILD_FILES)
        has_readme = any(split_ext(c.name)[0].casefold() == "readme" for c in root.children)
        docs = "docs"
        moved_docs = 0
        jars = []
        java_loose = []
        for f in self._loose_files(root):
            low = f.name.casefold()
            if low in JAVA_BUILD_FILES or low.startswith("gradlew") or low in ("gradle.properties", "lombok.config"):
                continue
            if f.category in ("documents", "presentations", "spreadsheets") and f.ext not in (".md", ".txt"):
                plan = self.fp(f)
                plan.new_dir = docs
                plan.reasons.append("Dokumentation im Ordner „docs“ sammeln (übliche Projektkonvention)")
                plan.confidence = min(plan.confidence, 0.6)
                moved_docs += 1
            elif f.ext == ".jar":
                jars.append(f)
            elif f.ext in (".java", ".kt"):
                java_loose.append(f)
            elif self.mode == "minecraft" and low in MINECRAFT_MARKERS | {"config.yml"} and has_build:
                if self.snap.get("src/main") is not None:
                    plan = self.fp(f)
                    plan.new_dir = "src/main/resources"
                    plan.reasons.append(
                        "Maven/Gradle packen nur Dateien aus src/main/resources in das Plugin-JAR"
                    )
                    plan.confidence = min(plan.confidence, 0.5)
        if moved_docs and has_readme:
            self.warnings.append("Falls die README auf verschobene Dokumente verlinkt, bitte die Links prüfen.")
        if jars:
            if has_build:
                self.questions.append(
                    f"{len(jars)} .jar-Datei(en) im Hauptordner ({', '.join(j.name for j in jars[:3])}). "
                    "Handelt es sich um Build-Ergebnisse oder Abhängigkeiten? Sie bleiben vorerst unverändert."
                )
            else:
                for j in jars:
                    plan = self.fp(j)
                    plan.new_dir = "libs"
                    plan.reasons.append("Bibliotheken (.jar) im Ordner „libs“ sammeln")
                    plan.confidence = min(plan.confidence, 0.45)
        if java_loose:
            if has_build:
                self.questions.append(
                    f"{len(java_loose)} Java-Datei(en) liegen außerhalb von src/. Da eine Build-Datei existiert, "
                    "könnte das gewollt sein – sie bleiben unverändert."
                )
            else:
                unknown = 0
                for f in java_loose:
                    info = self.contents.get(f.rel_path)
                    if info is not None and info.java_package:
                        plan = self.fp(f)
                        plan.new_dir = "src/main/java/" + info.java_package.replace(".", "/")
                        plan.reasons.append(
                            f"Standard-Maven-Struktur; Paket „{info.java_package}“ laut Dateiinhalt"
                        )
                        plan.confidence = min(plan.confidence, 0.55)
                    else:
                        unknown += 1
                if unknown:
                    self.questions.append(
                        f"{unknown} Java-Datei(en) liegen nicht in src/main/java. Der Paketpfad lässt sich nur aus "
                        "dem Inhalt bestimmen – aktivieren Sie dafür die Inhaltsanalyse."
                    )
        if not has_readme:
            self.warnings.append("Hinweis: Das Projekt hat keine README-Datei.")

    # -- Abschluss ---------------------------------------------------------
    def _finalize(self) -> tuple[Plan, set[str]]:
        ops: list[Operation] = []
        dir_op_ids: dict[str, str] = {}
        for key in sorted(self.dir_ops, key=lambda k: (k.count("/"), k)):
            e = self.snap.entries[key]
            new_name, reasons, conf = self.dir_ops[key]
            parent_final = self.dir_final.get(path_key(e.parent_rel), e.parent_rel)
            op = Operation(
                id="", type=RENAME, source=e.rel_path, target=join_rel(parent_final, new_name),
                reason="; ".join(reasons), confidence=conf, origin="builtin",
            )
            ops.append(op)
            dir_op_ids[id(op)] = key
        for path, reason, conf in self.create_ops:
            ops.append(Operation(id="", type=CREATE_FOLDER, target=path, reason=reason, confidence=conf, origin="builtin"))
        file_ops: list[Operation] = []
        for key, plan in self.file_plans.items():
            e = self.snap.entries[key]
            cur_dir = self.final_dir(self.snap.entries[path_key(e.parent_rel)])
            target_dir = plan.new_dir if plan.new_dir is not None else cur_dir
            name = plan.new_name or e.name
            target = join_rel(target_dir, name)
            if target == join_rel(cur_dir, e.name) or not plan.reasons:
                continue
            same_dir = path_key(target_dir) == path_key(cur_dir)
            file_ops.append(Operation(
                id="", type=RENAME if same_dir else MOVE, source=e.rel_path, target=target,
                reason="; ".join(dict.fromkeys(plan.reasons)), confidence=round(plan.confidence, 2),
                origin="builtin",
            ))
        # Reine Verschiebungen vor Umbenennungen: Bei Namenskonflikten wird so die
        # Umbenennung (und nicht das unveränderte Original) zurückgenommen.
        file_ops.sort(key=lambda o: (name_of(o.source or "") != name_of(o.target), o.target.count("/"), o.target.casefold()))
        ops.extend(file_ops)
        for i, op in enumerate(ops, 1):
            op.id = f"op{i}"
            op.accepted = (op.confidence or 0) >= ACCEPT_THRESHOLD

        plan = Plan(operations=ops, provider="builtin", language=self.lang, project_type=self.mode)
        # Konflikte auflösen: zuerst neuen Namen verwerfen, sonst Vorschlag verwerfen
        dropped = 0
        bad_dirs: set[str] = set()
        for _ in range(6):
            accepted_state = {op.id: op.accepted for op in plan.operations}
            for op in plan.operations:
                op.accepted = True
            res = resolve_plan(self.snap, plan)
            for op in plan.operations:
                op.accepted = accepted_state[op.id]
            bad = {i.op_id for i in res.issues if i.level == ERROR and i.op_id}
            if not bad:
                break
            keep = []
            for op in plan.operations:
                if op.id not in bad:
                    keep.append(op)
                    continue
                if id(op) in dir_op_ids:
                    bad_dirs.add(dir_op_ids[id(op)])
                    continue
                if op.source and op.type == MOVE and name_of(op.target) != name_of(op.source):
                    op.target = join_rel(parent_of(op.target), name_of(op.source))
                    op.warnings.append("Der neue Name hätte einen Konflikt verursacht – ursprünglicher Name bleibt.")
                    keep.append(op)
                    continue
                dropped += 1
            plan.operations = keep
            if bad_dirs:
                return plan, bad_dirs
        for i, op in enumerate(plan.operations, 1):
            op.id = f"op{i}"

        accepted_state = {op.id: op.accepted for op in plan.operations}
        for op in plan.operations:
            op.accepted = True
        n_new = len(resolve_plan(self.snap, plan).created_dirs)
        for op in plan.operations:
            op.accepted = accepted_state[op.id]
        self._compose_texts(plan, dropped, n_new)
        return plan, set()

    def _compose_texts(self, plan: Plan, dropped: int, n_new: int) -> None:
        det = self.detection

        def is_dir_op(o: Operation) -> bool:
            e = self.snap.get(o.source or "") if o.source else None
            return e is not None and e.is_dir

        n_dir = sum(1 for o in plan.operations if o.source and is_dir_op(o))
        n_files = sum(1 for o in plan.operations if o.source and not is_dir_op(o))
        lang_txt = "Deutsch" if self.lang == "de" else "Englisch"
        plan.summary = (
            f"Projektart: {PROJECT_LABELS_DE.get(self.mode, self.mode)} ({'; '.join(det.reasons)}). "
            f"Sprache der Namen: {lang_txt}{'' if self.lang_sure else ' (Standard)'}. "
            f"{len(plan.operations)} Vorschläge: {n_dir} Ordner umbenennen, {n_files} Dateien "
            f"verschieben/umbenennen, {n_new} neue Ordner."
        )
        if not plan.operations:
            plan.summary += " Die Struktur ist bereits gut organisiert – oder geschützt."
        plan.warnings.extend(self.warnings)
        if dropped:
            plan.warnings.append(f"{dropped} Vorschlag/Vorschläge wurden wegen Namenskonflikten verworfen.")
        if self.generic_files:
            examples = ", ".join(f"„{name_of(p)}“" for p in self.generic_files[:4])
            plan.questions.append(
                f"{len(self.generic_files)} Datei(en) haben nichtssagende Namen (z. B. {examples}). "
                "Am Namen allein ist der Inhalt nicht erkennbar – die Namen bleiben daher erhalten. "
                "Sie können sie in der Freigabe selbst umbenennen oder die Inhaltsanalyse aktivieren."
            )
        if self.generic_dirs:
            examples = ", ".join(f"„{name_of(p)}“" for p in self.generic_dirs[:4])
            plan.questions.append(
                f"{len(self.generic_dirs)} Ordner haben nichtssagende Namen (z. B. {examples}). Welche Inhalte "
                "gehören hinein? Ohne weitere Angaben bleiben die Namen unverändert."
            )
        if self.ambiguous_dates:
            plan.questions.append(
                "Mehrdeutige Datumsangaben (TT-MM oder MM-TT?) wurden nicht verändert: "
                + ", ".join(self.ambiguous_dates[:5])
            )
        if self.questions:
            plan.questions.extend(self.questions)
        low = sum(1 for o in plan.operations if not o.accepted)
        if low:
            plan.warnings.append(
                f"{low} Vorschlag/Vorschläge mit geringer Sicherheit sind nicht vorausgewählt – bitte gezielt prüfen."
            )


__all__ = ["BuiltinEngine", "resolve_language"]
