"""Zuordnung von Dateiendungen zu Dateitypen (Kategorien)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label_de: str
    label_en: str
    folder_de: str
    folder_en: str
    extensions: frozenset[str]
    #: Vorhandene Ordnernamen (kleingeschrieben, Trenner entfernt), die als
    #: passender Sammelordner für diese Kategorie gelten.
    folder_aliases: frozenset[str]

    def label(self, lang: str) -> str:
        return self.label_en if lang == "en" else self.label_de

    def folder(self, lang: str) -> str:
        return self.folder_en if lang == "en" else self.folder_de


def _c(key, label_de, label_en, folder_de, folder_en, exts, aliases) -> Category:
    return Category(
        key,
        label_de,
        label_en,
        folder_de,
        folder_en,
        frozenset(e.lower() for e in exts.split()),
        frozenset(aliases.split()),
    )


CATEGORIES: tuple[Category, ...] = (
    _c("documents", "Dokument", "Document", "Dokumente", "Documents",
       ".pdf .doc .docx .docm .odt .rtf .txt .md .tex .epub .pages .xps .wpd",
       "dokumente documents docs dokument document unterlagen texte texts papers"),
    _c("spreadsheets", "Tabelle", "Spreadsheet", "Tabellen", "Spreadsheets",
       ".xls .xlsx .xlsm .ods .csv .tsv .numbers",
       "tabellen spreadsheets excel tables"),
    _c("presentations", "Präsentation", "Presentation", "Präsentationen", "Presentations",
       ".ppt .pptx .pptm .odp .key",
       "präsentationen praesentationen presentations folien slides powerpoint"),
    _c("images", "Bild", "Image", "Bilder", "Images",
       ".jpg .jpeg .jfif .png .gif .bmp .tif .tiff .webp .heic .heif .avif .svg .ico "
       ".raw .cr2 .cr3 .nef .arw .dng .orf .rw2 .raf .psd .xcf .ai .eps .kra",
       "bilder images fotos photos pictures pics bild image grafiken graphics img"),
    _c("videos", "Video", "Video", "Videos", "Videos",
       ".mp4 .mov .avi .mkv .wmv .flv .webm .m4v .mpg .mpeg .mts .m2ts .3gp .vob",
       "videos video filme movies clips"),
    _c("audio", "Audio", "Audio", "Audio", "Audio",
       ".mp3 .wav .flac .aac .ogg .oga .m4a .wma .opus .aif .aiff .mid .midi",
       "audio musik music sounds sound töne toene"),
    _c("archives", "Archivdatei", "Compressed archive", "Archivdateien", "Compressed",
       ".zip .rar .7z .tar .gz .tgz .bz2 .xz .zst .iso .cab",
       "archivdateien compressed zip zips zipdateien"),
    _c("programs", "Programm", "Program", "Programme", "Programs",
       ".exe .msi .msix .appx .appxbundle .bat .cmd .ps1 .vbs .reg .jar",
       "programme programs installer installers setups setup software apps"),
    _c("code", "Quellcode", "Source code", "Code", "Code",
       ".py .pyw .java .kt .kts .js .mjs .cjs .ts .tsx .jsx .c .h .cpp .hpp .cc .cs .go .rs "
       ".rb .php .swift .lua .sh .sql .html .htm .css .scss .json .xml .yml .yaml .toml .ini "
       ".cfg .gradle .properties .ipynb .vue .dart",
       "code quellcode source sources src scripts skripte"),
    _c("fonts", "Schriftart", "Font", "Schriftarten", "Fonts",
       ".ttf .otf .woff .woff2 .fon",
       "schriftarten fonts schriften"),
    _c("models3d", "3D-Modell", "3D model", "3D-Modelle", "3D_Models",
       ".stl .obj .fbx .blend .3mf .gltf .glb .step .stp .dae .3ds",
       "3dmodelle 3dmodels 3d modelle"),
    _c("video_projects", "Videoprojekt", "Video project", "Projektdateien", "Project_Files",
       ".prproj .aep .aepx .drp .veg .vf .kdenlive .mlt .fcpxml .wlmp .mswmm .pproj",
       "projektdateien projectfiles projekte_video"),
    _c("shortcuts", "Verknüpfung", "Shortcut", "Verknüpfungen", "Shortcuts",
       ".lnk .url .webloc",
       "verknüpfungen verknuepfungen shortcuts links"),
)

OTHER = Category(
    "other", "Sonstige Datei", "Other file", "Sonstiges", "Misc", frozenset(), frozenset()
)

_BY_EXT: dict[str, Category] = {}
for _cat in CATEGORIES:
    for _ext in _cat.extensions:
        _BY_EXT.setdefault(_ext, _cat)

BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
BY_KEY[OTHER.key] = OTHER

#: Kategorien, die als zusammengehörende "Medien" gelten (z. B. Fotoalben mit Videos).
MEDIA_KEYS = frozenset({"images", "videos"})

#: Kategorien, deren Dateien die integrierte KI nie umbenennt (Namen werden referenziert).
NO_RENAME_KEYS = frozenset({"code", "programs", "fonts"})

RAW_PHOTO_EXTS = frozenset(".raw .cr2 .cr3 .nef .arw .dng .orf .rw2 .raf".split())


def category_for(ext: str) -> Category:
    return _BY_EXT.get(ext.lower(), OTHER)


def category_by_key(key: str) -> Category:
    return BY_KEY.get(key, OTHER)


def _alias_key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def category_for_folder_name(name: str) -> Category | None:
    """Erkennt, ob ein vorhandener Ordnername ein Sammelordner für eine Kategorie ist."""
    key = _alias_key(name)
    if not key:
        return None
    for cat in CATEGORIES:
        aliases = {_alias_key(a) for a in cat.folder_aliases}
        aliases.add(_alias_key(cat.folder_de))
        aliases.add(_alias_key(cat.folder_en))
        if key in aliases:
            return cat
    return None


def describe_type(ext: str, lang: str = "de") -> str:
    cat = category_for(ext)
    label = cat.label(lang)
    return f"{label} ({ext})" if ext else label
