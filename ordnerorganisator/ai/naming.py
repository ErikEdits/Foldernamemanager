"""Namensregeln der integrierten KI: Bereinigen, Vereinheitlichen, Übersetzen.

Die Funktionen arbeiten ausschließlich mit dem *Namen*. Sie erfinden keine
Inhalte: Ein nichtssagender Name wie ``datei1.txt`` wird nicht „geraten“,
sondern als unklar markiert.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from ..settings import UserRules
from ..windows_names import sanitize_name, split_ext

# ---------------------------------------------------------------------------
# Wörterbuch für typische Ordner- und Dateinamen (Deutsch ↔ Englisch)
# ---------------------------------------------------------------------------

DE_EN: dict[str, str] = {
    "bilder": "images", "bild": "image", "fotos": "photos", "foto": "photo",
    "dokumente": "documents", "dokument": "document", "unterlagen": "documents",
    "musik": "music", "filme": "movies", "film": "movie",
    "rechnungen": "invoices", "rechnung": "invoice", "quittungen": "receipts", "belege": "receipts",
    "verträge": "contracts", "vertrag": "contract", "projekte": "projects", "projekt": "project",
    "sonstiges": "misc", "verschiedenes": "misc", "sonstige": "other",
    "vorlagen": "templates", "vorlage": "template", "notizen": "notes", "notiz": "note",
    "präsentationen": "presentations", "präsentation": "presentation",
    "tabellen": "spreadsheets", "tabelle": "spreadsheet",
    "programme": "programs", "programm": "program", "schriftarten": "fonts",
    "neu": "new", "neue": "new", "neuer": "new", "alt": "old", "alte": "old",
    "entwurf": "draft", "entwürfe": "drafts", "kopie": "copy", "privat": "private",
    "arbeit": "work", "schule": "school", "urlaub": "vacation", "reisen": "travel",
    "steuern": "taxes", "steuer": "tax", "versicherung": "insurance", "versicherungen": "insurances",
    "briefe": "letters", "brief": "letter", "bewerbungen": "applications", "bewerbung": "application",
    "zeugnisse": "certificates", "zeugnis": "certificate", "archiv": "archive",
    "sicherung": "backup", "sicherungen": "backups", "quellcode": "source", "skripte": "scripts",
    "einstellungen": "settings", "grafiken": "graphics", "grafik": "graphic",
    "aufnahmen": "recordings", "aufnahme": "recording", "fertig": "finished",
    "ordner": "folder", "dateien": "files", "datei": "file", "daten": "data",
    "spiele": "games", "spiel": "game", "bücher": "books", "buch": "book",
    "handbuch": "manual", "handbücher": "manuals", "anleitungen": "guides", "anleitung": "guide",
    "rezepte": "recipes", "rezept": "recipe", "finanzen": "finances", "konto": "account",
    "lebenslauf": "resume", "gehalt": "salary", "angebote": "quotes", "angebot": "quote",
    "kunden": "customers", "kunde": "customer", "berichte": "reports", "bericht": "report",
    "texte": "texts", "unsortiert": "unsorted", "wichtig": "important", "erledigt": "done",
    "familie": "family", "freunde": "friends", "hochzeit": "wedding", "geburtstag": "birthday",
    "weihnachten": "christmas", "ausgaben": "expenses", "einnahmen": "income",
    "kurs": "course", "kurse": "courses", "studium": "studies", "hausaufgaben": "homework",
    "vorlesung": "lecture", "vorlesungen": "lectures", "übungen": "exercises", "übung": "exercise",
    "lösungen": "solutions", "lösung": "solution", "quellen": "sources", "literatur": "literature",
    "entwicklung": "development", "hintergründe": "backgrounds", "hintergrund": "background",
    "symbole": "icons", "rohmaterial": "footage", "videomaterial": "footage",
    "schnitt": "editing", "ton": "sound", "töne": "sounds", "einkauf": "purchasing",
    "verkauf": "sales", "personal": "staff", "intern": "internal", "extern": "external",
    "öffentlich": "public", "sonstig": "other", "allgemein": "general", "persönlich": "personal",
    "geschäftlich": "business", "kontakte": "contacts", "termine": "appointments",
}

#: Wörter, die in beiden Sprachen gleich (üblich) sind – werden nie übersetzt.
NEUTRAL = frozenset(
    """downloads download videos video audio code backup backups export exports import imports
    final design designs logos logo media scans scan screenshots screenshot test tests temp tmp
    text bank team software apps app web website marketing social office home cloud pdf pdfs
    docs doc src assets icons tools tool games demo demos release releases build builds
    plugin plugins mods mod server client config configs data input output html css js
    archiv archive sound sounds mix master stems raw cache logs log misc footage""".split()
)

EN_DE: dict[str, str] = {}
for _de, _en in DE_EN.items():
    if _en not in NEUTRAL:
        EN_DE.setdefault(_en, _de)
EN_DE.update({"documents": "dokumente", "new": "neu", "old": "alt", "misc": "sonstiges", "other": "sonstiges"})

_DE_ONLY_EXTRA = {"und", "oder", "für", "mit", "von", "der", "die", "das", "meine", "mein", "unser"}
_EN_ONLY_EXTRA = {"and", "the", "for", "with", "my", "our", "of"}

DE_WORDS = (set(DE_EN) | _DE_ONLY_EXTRA) - NEUTRAL
EN_WORDS = (set(DE_EN.values()) | set(EN_DE) | _EN_ONLY_EXTRA) - NEUTRAL
_BOTH = DE_WORDS & EN_WORDS
DE_WORDS -= _BOTH
EN_WORDS -= _BOTH

STOPWORDS = frozenset(
    "und oder von vom bis der die das für mit im in am an auf zu zum zur beim and or of the for with on at to a vs".split()
)

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}

# ---------------------------------------------------------------------------
# Muster
# ---------------------------------------------------------------------------

_SEP_RE = re.compile(r"([ _\-]+)")
_PREFIX_RE = re.compile(r"^[ _\-!#@~+]+")
_VERSION_RE = re.compile(r"^v\d", re.I)
_PLACEHOLDER_RE = re.compile("(\\d+)")

_COPY_MARKERS = [
    re.compile(r"^(?:kopie von|copy of)\s+(.+)$", re.I),
    re.compile(r"^(.+?)\s*[-_]\s*(?:kopie|copy)(?:\s*\(\d+\))?$", re.I),
    re.compile(r"^(.+?)\s*\((?:kopie|copy)(?:\s*\d+)?\)$", re.I),
    re.compile(r"^(.+?)\s*\(\d{1,3}\)$"),
]

_GENERIC_RES = [
    re.compile(
        r"^(?:neue[rs]?\s+|new\s+)?(?:microsoft\s+)?(?:word[\s\-]?|excel[\s\-]?|powerpoint[\s\-]?)?"
        r"(?:textdokument|text\s*document|dokument|document|doc|datei|file|ordner|folder|tabelle|"
        r"arbeitsmappe|mappe|book|worksheet|präsentation|presentation|bild|image|img|foto|photo|pic|zeichnung|drawing|"
        r"unbenannt|untitled|unnamed|ohne\s+titel|noname|no\s+name|neu|new|test|temp|tmp|scan|"
        r"aufnahme|recording|audio|video|export|download|output|ausgabe|sonstiges|misc|stuff|kram|zeug|"
        r"entwurf|draft|notiz|note|text)"
        r"[\s_\-]*\(?\d*\)?$",
        re.I,
    ),
    re.compile(r"^\d{1,6}$"),
    re.compile(r"^[a-z]$", re.I),
    re.compile(r"^(?:asdf+|qwert[yz]?|xyz|abc|aaa+|bla(?:bla)*|foo|bar|baz)\d*$", re.I),
]

_CAMERA_RE = re.compile(
    r"^(?:img|vid|dsc|dscn|dscf|pxl|mvi|gopr|gp|dji|pic|imag|cimg|sam|wp|_mg|mov|"
    r"screenshot|bildschirmfoto|whatsapp image|whatsapp video|signal|photo|pano)[\s_\-]?\d",
    re.I,
)

_DATE_DOTTED = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d])")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})([-_.])(\d{1,2})\2(\d{1,2})(?!\d)")
_DATE_DMY_DASH = re.compile(r"(?<!\d)(\d{1,2})([-_])(\d{1,2})\2(\d{4})(?!\d)")
_DATE_COMPACT = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)")
_NUM_RANGE = re.compile(r"(?<![\w])\d+(?:-\d+)+(?![\w])")


def _valid_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000 if y <= (date.today().year % 100) + 1 else 1900
    if not (1900 <= y <= date.today().year + 1):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def format_date(d: date, fmt: str) -> str:
    if fmt == "compact":
        return d.strftime("%Y%m%d")
    if fmt == "german":
        return d.strftime("%d.%m.%Y")
    return d.isoformat()


_DATE_MARK_RE = re.compile("\ue002(\\d+)\ue003")


@dataclass
class DateResult:
    #: Text, in dem erkannte Datumsangaben durch Marker ersetzt sind
    text: str
    dates: list[str] = field(default_factory=list)  # formatierte Datumswerte
    changed: bool = False
    ambiguous: list[str] = field(default_factory=list)

    def plain(self) -> str:
        return _DATE_MARK_RE.sub(lambda m: self.dates[int(m.group(1))], self.text)


def reformat_dates(stem: str, fmt: str) -> DateResult:
    """Vereinheitlicht Datumsangaben. Kompakte Kameradaten (20240312) bleiben unverändert."""
    found: list[str] = []
    ambiguous: list[str] = []
    changed = False

    def _mark(value: str) -> str:
        found.append(value)
        return f"\ue002{len(found) - 1}\ue003"

    def repl_dotted(m: re.Match) -> str:
        nonlocal changed
        d = _valid_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d is None:
            return m.group(0)
        new = format_date(d, fmt)
        changed |= new != m.group(0)
        return _mark(new)

    def repl_ymd(m: re.Match) -> str:
        nonlocal changed
        d = _valid_date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        if d is None:
            return m.group(0)
        new = format_date(d, fmt)
        changed |= new != m.group(0)
        return _mark(new)

    def repl_dmy(m: re.Match) -> str:
        nonlocal changed
        a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
        if a > 12 and b <= 12:
            d = _valid_date(y, b, a)
        elif b > 12 and a <= 12:
            d = _valid_date(y, a, b)
        else:
            if a <= 12 and b <= 12 and a != b:
                ambiguous.append(m.group(0))
            d = _valid_date(y, b, a) if a == b else None
        if d is None:
            return m.group(0)
        new = format_date(d, fmt)
        changed |= new != m.group(0)
        return _mark(new)

    text = _DATE_DOTTED.sub(repl_dotted, stem)
    text = _DATE_YMD.sub(repl_ymd, text)
    text = _DATE_DMY_DASH.sub(repl_dmy, text)
    return DateResult(text=text, dates=found, changed=changed, ambiguous=ambiguous)


def extract_date(name: str) -> date | None:
    """Liest ein Datum aus einem Dateinamen (z. B. IMG_20230715_101500.jpg)."""
    stem = split_ext(name)[0]
    for rx, order in ((_DATE_YMD, "ymd"), (_DATE_DOTTED, "dmy"), (_DATE_COMPACT, "compact")):
        m = rx.search(stem)
        if not m:
            continue
        if order == "ymd":
            d = _valid_date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        elif order == "dmy":
            d = _valid_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        else:
            d = _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d is not None:
            return d
    return None


def strip_copy_marker(stem: str) -> tuple[str, bool]:
    for rx in _COPY_MARKERS:
        m = rx.match(stem)
        if m and m.group(1).strip():
            return m.group(1).strip(), True
    return stem, False


def is_generic_name(name: str, is_dir: bool = False) -> bool:
    stem = name if is_dir else split_ext(name)[0]
    stem, _ = strip_copy_marker(stem.strip())
    return any(rx.match(stem.strip()) for rx in _GENERIC_RES)


def is_camera_name(name: str) -> bool:
    return bool(_CAMERA_RE.match(split_ext(name)[0]))


def transliterate(text: str) -> str:
    return "".join(_UMLAUTS.get(c, c) for c in text)


# ---------------------------------------------------------------------------
# Wörter zerlegen und zusammensetzen
# ---------------------------------------------------------------------------


@dataclass
class Tokens:
    prefix: str
    words: list[str]
    seps: list[str]  # Trenner *vor* jedem Wort (seps[0] == "")
    protected: list[str]

    def restore(self, text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: self.protected[int(m.group(1))], text)


def tokenize(stem: str, dates: list[str] | None = None) -> Tokens:
    """Zerlegt einen Namen in Wörter. Datumsangaben und Zahlenbereiche bleiben am Stück."""
    protected: list[str] = []

    def protect_date(m: re.Match) -> str:
        protected.append((dates or [])[int(m.group(1))])
        return f"\ue000{len(protected) - 1}\ue001"

    def protect_range(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\ue000{len(protected) - 1}\ue001"

    text = _DATE_MARK_RE.sub(protect_date, stem)
    text = _NUM_RANGE.sub(protect_range, text)
    pm = _PREFIX_RE.match(text)
    prefix = pm.group(0) if pm else ""
    text = text[len(prefix):]
    parts = _SEP_RE.split(text)
    words: list[str] = []
    seps: list[str] = []
    pending_sep = ""
    for i, part in enumerate(parts):
        if i % 2 == 1:
            pending_sep = part
            continue
        if part == "":
            continue
        words.append(part)
        seps.append(pending_sep if words[:-1] else "")
        pending_sep = ""
    return Tokens(prefix=prefix, words=words, seps=seps, protected=protected)


def dedupe_words(tok: Tokens) -> bool:
    """Entfernt direkt doppelte Wörter („final_final“ → „final“)."""
    changed = False
    words, seps = [], []
    for w, s in zip(tok.words, tok.seps):
        if words and w.casefold() == words[-1].casefold() and not w.isdigit():
            changed = True
            continue
        words.append(w)
        seps.append(s if words[:-1] else "")
    tok.words, tok.seps = words, seps
    return changed


def _cap(word: str, first: bool) -> str:
    if not word or not word[0].isalpha():
        return word
    if word.islower():
        if not first and word in STOPWORDS:
            return word
        if _VERSION_RE.match(word):
            return word
        return word[0].upper() + word[1:]
    return word


def join_tokens(tok: Tokens, style: str) -> str:
    if not tok.words:
        return tok.restore(tok.prefix).strip()
    if style == "keep":
        out = tok.words[0]
        for w, s in zip(tok.words[1:], tok.seps[1:]):
            s = re.sub(r"\s+", " ", s)
            s = re.sub(r"_+", "_", s)
            s = re.sub(r"-+", "-", s)
            out += (s or " ") + w
    elif style == "title_underscore":
        out = "_".join(_cap(w, i == 0) for i, w in enumerate(tok.words))
    elif style == "title_space":
        out = " ".join(_cap(w, i == 0) for i, w in enumerate(tok.words))
    elif style == "kebab":
        out = "-".join(w.lower() for w in tok.words)
    elif style == "snake":
        out = "_".join(w.lower() for w in tok.words)
    else:
        raise ValueError(f"Unbekannte Namenskonvention: {style}")
    return tok.restore(tok.prefix + out)


def style_name(name: str, style: str) -> str:
    """Wendet eine Namenskonvention auf einen (neuen) Ordnernamen an."""
    return join_tokens(tokenize(name), style)


# ---------------------------------------------------------------------------
# Sprache
# ---------------------------------------------------------------------------


def detect_language(names: Iterable[str]) -> str | None:
    counts: Counter[str] = Counter()
    for name in names:
        for w in re.split(r"[^\wäöüÄÖÜß]+", split_ext(name)[0].casefold()):
            if len(w) < 2:
                continue
            if w in DE_WORDS or any(c in w for c in "äöüß"):
                counts["de"] += 1
            elif w in EN_WORDS:
                counts["en"] += 1
    if not counts:
        return None
    if counts["de"] == counts["en"]:
        return None
    return "de" if counts["de"] > counts["en"] else "en"


def translate_words(words: list[str], lang: str) -> list[str] | None:
    """Übersetzt nur, wenn *alle* Wörter bekannt sind – sonst ``None`` (keine Mischnamen)."""
    out = []
    changed = False
    for w in words:
        if "" in w or not any(c.isalpha() for c in w) or _VERSION_RE.match(w):
            out.append(w)
            continue
        low = w.casefold()
        if low in NEUTRAL:
            out.append(w)
            continue
        if lang == "en":
            if low in DE_EN:
                new = DE_EN[low]
            elif low in EN_WORDS:
                new = low
            else:
                return None
        else:
            if low in EN_DE:
                new = EN_DE[low]
            elif low in DE_WORDS or low in DE_EN:
                new = low
            else:
                return None
        if new != low:
            changed = True
            if w[0].isupper():
                new = new[0].upper() + new[1:]
            out.append(new)
        else:
            out.append(w)
    return out if changed else None


# ---------------------------------------------------------------------------
# Namensvorschlag
# ---------------------------------------------------------------------------

STYLE_LABELS = {
    "title_underscore": "Wörter_Mit_Unterstrich",
    "title_space": "Wörter Mit Leerzeichen",
    "kebab": "kleinbuchstaben-mit-bindestrich",
    "snake": "kleinbuchstaben_mit_unterstrich",
}
DATE_LABELS = {"iso": "JJJJ-MM-TT", "compact": "JJJJMMTT", "german": "TT.MM.JJJJ"}


@dataclass
class NameChange:
    new_name: str
    reasons: list[str]
    confidence: float
    removed_copy_marker: bool = False
    ambiguous_dates: list[str] = field(default_factory=list)


def suggest_name(
    name: str,
    *,
    is_dir: bool,
    rules: UserRules,
    lang: str,
    translate: bool = False,
    restyle: bool = True,
) -> NameChange | None:
    """Schlägt einen bereinigten Namen vor oder liefert ``None``, wenn nichts zu tun ist."""
    stem, ext = (name, "") if is_dir else split_ext(name)
    reasons: list[str] = []
    confidence = 0.85
    removed_marker = False

    cleaned = re.sub(r"\s+", " ", stem).strip().rstrip(".")
    if cleaned != stem:
        reasons.append("Überflüssige Leerzeichen/Punkte entfernt")

    if rules.remove_copy_markers:
        stripped, removed = strip_copy_marker(cleaned)
        if removed:
            cleaned = stripped
            removed_marker = True
            confidence = 0.6
            reasons.append("Kopie-Kennzeichnung entfernt (z. B. „- Kopie“, „(1)“)")

    dres = reformat_dates(cleaned, rules.date_format)
    if dres.changed:
        reasons.append(f"Datum einheitlich als {DATE_LABELS.get(rules.date_format, rules.date_format)}")

    camera = is_camera_name(cleaned)
    tok = tokenize(dres.text, dres.dates)
    base_keep = join_tokens(tok, "keep")
    if dedupe_words(tok):
        reasons.append("Doppelte Wörter entfernt")

    if translate and is_dir:
        translated = translate_words(tok.words, lang)
        if translated is not None:
            tok.words = translated
            reasons.append("Einheitlich " + ("englisch" if lang == "en" else "deutsch") + " benannt")

    style = rules.naming_style if (restyle and not camera) else "keep"
    new_stem = join_tokens(tok, style)
    if style != "keep" and new_stem != join_tokens(tok, "keep"):
        reasons.append(f"Namenskonvention „{STYLE_LABELS.get(style, style)}“")
    elif style == "keep" and new_stem != base_keep and "Doppelte Wörter entfernt" not in reasons:
        reasons.append("Trennzeichen bereinigt")

    if rules.transliterate_umlauts:
        t = transliterate(new_stem)
        if t != new_stem:
            new_stem = t
            reasons.append("Umlaute ersetzt (ä → ae …)")

    new_ext = ext
    if ext and rules.lowercase_extensions and ext != ext.lower():
        new_ext = ext.lower()
        reasons.append("Dateiendung kleingeschrieben")

    if not new_stem.strip():
        return None
    new_name = sanitize_name(new_stem + new_ext)
    if new_name == name:
        return None
    return NameChange(
        new_name=new_name,
        reasons=reasons or ["Name vereinheitlicht"],
        confidence=confidence,
        removed_copy_marker=removed_marker,
        ambiguous_dates=dres.ambiguous,
    )


def title_to_filename(title: str, ext: str, rules: UserRules) -> str | None:
    """Erzeugt aus einer Überschrift (Inhaltsanalyse) einen Dateinamen."""
    words = re.findall(r"[\wäöüÄÖÜß]+(?:[.\-][\wäöüÄÖÜß]+)*", title)
    words = [w for w in words if w][:7]
    if not words or sum(len(w) for w in words) < 3:
        return None
    text = " ".join(words)[:60]
    style = rules.naming_style if rules.naming_style != "keep" else "title_space"
    stem = join_tokens(tokenize(text), style)
    if rules.transliterate_umlauts:
        stem = transliterate(stem)
    return sanitize_name(stem + ext.lower())
