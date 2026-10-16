"""Erkennung der Projektart (Spezialmodi)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..models import Entry, Snapshot

PROJECT_LABELS_DE = {
    "general": "Allgemeine Dateiablage",
    "java": "Java-Projekt",
    "minecraft": "Minecraft-Plugin",
    "video": "Videoprojekt",
    "photos": "Fotosammlung",
}

#: Dateien, die einen Ordner als Software-Projekt kennzeichnen
CODE_MARKERS = frozenset(
    n.casefold()
    for n in (
        "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
        "gradlew", "package.json", "cargo.toml", "go.mod", "pyproject.toml", "setup.py",
        "cmakelists.txt", "makefile", "composer.json", "gemfile", "build.xml", "meson.build",
        "plugin.yml", "paper-plugin.yml", "bungee.yml", "mod.json", "fabric.mod.json",
        "mods.toml", "project.godot", ".git",
    )
)
CODE_MARKER_SUFFIXES = (".sln", ".csproj", ".vcxproj", ".xcodeproj", ".uproject", ".iml")

MINECRAFT_MARKERS = frozenset({"plugin.yml", "paper-plugin.yml", "bungee.yml", "velocity-plugin.json"})
JAVA_BUILD_FILES = frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "build.xml"})


@dataclass
class Detection:
    mode: str
    confidence: float
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return PROJECT_LABELS_DE.get(self.mode, self.mode)


def is_code_project_dir(entry: Entry) -> bool:
    for c in entry.children:
        low = c.name.casefold()
        if low in CODE_MARKERS or low.endswith(CODE_MARKER_SUFFIXES):
            return True
    return False


def is_video_project_dir(entry: Entry) -> bool:
    return any((not c.is_dir) and c.category == "video_projects" for c in entry.children)


def detect_project_type(snapshot: Snapshot) -> Detection:
    root = snapshot.root
    root_names = {c.name.casefold() for c in root.children}
    files = [e for e in snapshot.files() if not e.is_special and not snapshot.is_protected(e.rel_path)]
    cats = Counter(e.category for e in files)
    n = max(1, len(files))
    all_names = {e.name.casefold() for e in files}

    has_build = bool(root_names & JAVA_BUILD_FILES)
    java_files = cats.get("code", 0) and sum(1 for e in files if e.ext in (".java", ".kt"))
    has_src_main = snapshot.get("src/main/java") is not None or snapshot.get("src/main/kotlin") is not None

    if all_names & MINECRAFT_MARKERS and (has_build or java_files):
        found = sorted(all_names & MINECRAFT_MARKERS)
        return Detection("minecraft", 0.9, [f"{found[0]} gefunden", "Java-Quellcode/Build-Datei vorhanden"])
    if has_build or (java_files and has_src_main):
        reasons = []
        if has_build:
            reasons.append("Build-Datei (Maven/Gradle/Ant) im Hauptordner")
        if has_src_main:
            reasons.append("Ordner src/main/java vorhanden")
        return Detection("java", 0.85, reasons)
    if java_files and java_files >= max(3, n * 0.5):
        return Detection("java", 0.6, [f"{java_files} Java-Dateien"])

    video_projects = cats.get("video_projects", 0)
    videos = cats.get("videos", 0)
    if video_projects:
        return Detection("video", 0.85, [f"{video_projects} Videoschnitt-Projektdatei(en) gefunden"])
    if videos >= 3 and videos / n >= 0.4 and cats.get("images", 0) / n < 0.5:
        return Detection("video", 0.55, [f"{videos} Videodateien ({videos * 100 // n} % der Dateien)"])

    images = cats.get("images", 0)
    media = images + videos
    if images >= 10 and media / n >= 0.7:
        return Detection("photos", 0.75, [f"{images} Bilder ({media * 100 // n} % Fotos/Videos)"])

    return Detection("general", 0.6, ["Keine spezielle Projektart erkannt"])
