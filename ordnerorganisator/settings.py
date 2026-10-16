"""Benutzereinstellungen, eigene Regeln und Vorlagen.

API-Keys werden hier **nie** gespeichert – dafür ist :mod:`secrets_store` zuständig.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import app_data_dir

LANGUAGES = {"auto": "Automatisch (wie vorhandene Namen)", "de": "Deutsch", "en": "Englisch"}

NAMING_STYLES = {
    "title_underscore": "Wörter_Mit_Unterstrich (Project_Notes)",
    "title_space": "Wörter Mit Leerzeichen (Project Notes)",
    "kebab": "kleinbuchstaben-mit-bindestrich (project-notes)",
    "snake": "kleinbuchstaben_mit_unterstrich (project_notes)",
    "keep": "Schreibweise beibehalten (nur bereinigen)",
}

DATE_FORMATS = {
    "iso": "JJJJ-MM-TT (2024-03-12, sortierbar)",
    "compact": "JJJJMMTT (20240312)",
    "german": "TT.MM.JJJJ (12.03.2024)",
}

PROJECT_MODES = {
    "auto": "Automatisch erkennen",
    "general": "Allgemeine Dateiablage",
    "java": "Java-Projekt",
    "minecraft": "Minecraft-Plugin",
    "video": "Videoprojekt",
    "photos": "Fotosammlung",
}

PROVIDERS = {
    "builtin": "Integrierte KI (kostenlos, offline)",
    "local_model": "Lokales Modell (Modelldatei oder lokale Laufzeit)",
    "api": "API-Anbieter (z. B. OpenRouter)",
    "exchange": "Cloud-KI per Export/Import (z. B. ChatGPT)",
}


@dataclass
class UserRules:
    language: str = "auto"
    translate_existing: bool = True
    naming_style: str = "title_underscore"
    date_format: str = "iso"
    lowercase_extensions: bool = True
    transliterate_umlauts: bool = False
    remove_copy_markers: bool = True
    rename_files: bool = True
    rename_folders: bool = True
    group_by_type: bool = True
    min_files_for_grouping: int = 4
    project_mode: str = "auto"
    #: Bevorzugte Ordnernamen je Kategorie, z. B. {"images": "Fotos"}
    preferred_folder_names: dict[str, str] = field(default_factory=dict)
    #: Freitext-Regeln, die an externe KIs weitergegeben werden
    custom_instructions: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "UserRules":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class ApiSettings:
    provider_kind: str = "openrouter"  # openrouter | custom
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = ""
    only_free_models: bool = True
    timeout_s: int = 180

    @classmethod
    def from_dict(cls, data: dict) -> "ApiSettings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class LocalModelSettings:
    kind: str = "server"  # server (OpenAI-kompatibel, z. B. llama.cpp, LM Studio) | gguf
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = ""
    gguf_path: str = ""
    context_size: int = 8192

    @classmethod
    def from_dict(cls, data: dict) -> "LocalModelSettings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class AppSettings:
    rules: UserRules = field(default_factory=UserRules)
    provider: str = "builtin"
    #: Hauptschalter: Externe (Internet-)KI ist standardmäßig aus.
    external_ai_enabled: bool = False
    api: ApiSettings = field(default_factory=ApiSettings)
    local_model: LocalModelSettings = field(default_factory=LocalModelSettings)
    #: Zielordner für Kopien; leer = Desktop
    output_dir: str = ""
    include_hidden_in_package: bool = False
    include_absolute_root: bool = False
    max_package_chars: int = 60000
    content_analysis_enabled: bool = False
    show_hidden_in_tree: bool = True
    recent_folders: list[str] = field(default_factory=list)
    templates: dict[str, dict] = field(default_factory=dict)

    # -- Laden/Speichern ---------------------------------------------------
    @staticmethod
    def file_path() -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        p = cls.file_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            if f.name == "rules":
                s.rules = UserRules.from_dict(data["rules"])
            elif f.name == "api":
                s.api = ApiSettings.from_dict(data["api"])
            elif f.name == "local_model":
                s.local_model = LocalModelSettings.from_dict(data["local_model"])
            else:
                setattr(s, f.name, data[f.name])
        return s

    def save(self) -> None:
        p = self.file_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def remember_folder(self, path: str) -> None:
        self.recent_folders = [path] + [p for p in self.recent_folders if p != path]
        self.recent_folders = self.recent_folders[:10]

    # -- Vorlagen ----------------------------------------------------------
    def save_template(self, name: str, rules: UserRules | None = None) -> None:
        self.templates[name] = asdict(rules or self.rules)

    def load_template(self, name: str) -> UserRules:
        return UserRules.from_dict(self.templates[name])

    def delete_template(self, name: str) -> None:
        self.templates.pop(name, None)
