"""Optionale Inhaltsanalyse – nur für ausdrücklich ausgewählte Dateien.

Es werden ausschließlich kleine Ausschnitte gelesen (Überschrift, erste
Zeilen, Titel aus Metadaten). Die Dateien werden dabei nur gelesen.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from html import unescape

from .ai.naming import is_generic_name
from .models import Snapshot
from .paths import long_path

MAX_READ = 64 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024

TEXT_EXTS = frozenset(
    ".txt .md .markdown .csv .tsv .log .json .xml .yml .yaml .ini .cfg .rst .tex .html .htm "
    ".java .kt .py .js .ts .cs .c .cpp .h .properties .bat .ps1 .sh .sql".split()
)
OFFICE_EXTS = frozenset(".docx .docm .pptx .xlsx .xlsm .odt .odp .ods".split())
SUPPORTED_EXTS = TEXT_EXTS | OFFICE_EXTS | {".pdf"}

_GENERIC_TITLES = re.compile(
    r"^(microsoft (word|excel|powerpoint) - .*|untitled|unbenannt|dokument\d*|document\d*|"
    r"tabelle\d*|sheet\d*|mappe\d*|folie \d+|slide \d+|präsentation\d*|presentation\d*)$",
    re.I,
)


@dataclass
class ContentInfo:
    rel_path: str
    kind: str = ""
    title: str = ""
    excerpt: str = ""
    java_package: str = ""
    error: str = ""


def supports(ext: str) -> bool:
    return ext.lower() in SUPPORTED_EXTS


def suggested_candidates(snapshot: Snapshot) -> list[str]:
    """Dateien, bei denen eine Inhaltsanalyse besonders hilfreich wäre."""
    out = []
    for e in snapshot.files():
        if e.is_special or e.is_link or snapshot.is_protected(e.rel_path) or not supports(e.ext):
            continue
        if is_generic_name(e.name) or (e.ext == ".java" and "/src/" not in f"/{e.rel_path}"):
            out.append(e.rel_path)
    return out


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _clean_title(text: str) -> str:
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    text = text.lstrip("#*=-_ \t>").strip()
    text = re.sub(r"[*_`]+", "", text)
    if len(text) > 80:
        text = text[:80].rsplit(" ", 1)[0]
    if not text or _GENERIC_TITLES.match(text) or sum(c.isalpha() for c in text) < 3:
        return ""
    if re.match(r"^(https?://|www\.|[{\[<]|import |package |#include|<\?xml|<!doctype)", text, re.I):
        return ""
    return text


def _xml_text(xml: str, tag_regex: str) -> list[str]:
    paras = []
    for m in re.finditer(tag_regex, xml, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(0))
        text = unescape(text).strip()
        if text:
            paras.append(text)
    return paras


def _core_title(z: zipfile.ZipFile) -> str:
    for name in ("docProps/core.xml", "meta.xml"):
        if name in z.namelist():
            xml = z.read(name)[:MAX_READ].decode("utf-8", "replace")
            m = re.search(r"<dc:title>(.*?)</dc:title>", xml, re.S)
            if m:
                return _clean_title(m.group(1))
    return ""


def _office(path: str, ext: str) -> ContentInfo:
    info = ContentInfo(rel_path="", kind="office")
    with zipfile.ZipFile(long_path(path)) as z:
        info.title = _core_title(z)
        names = z.namelist()
        paras: list[str] = []
        if ext in (".docx", ".docm") and "word/document.xml" in names:
            xml = z.read("word/document.xml")[: MAX_READ * 4].decode("utf-8", "replace")
            paras = _xml_text(xml, r"<w:p[ >].*?</w:p>")
        elif ext == ".pptx":
            slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
            if slides:
                xml = z.read(slides[0])[: MAX_READ * 2].decode("utf-8", "replace")
                paras = _xml_text(xml, r"<a:p>.*?</a:p>")
        elif ext in (".xlsx", ".xlsm") and "xl/workbook.xml" in names:
            xml = z.read("xl/workbook.xml")[:MAX_READ].decode("utf-8", "replace")
            paras = [unescape(m) for m in re.findall(r'<sheet [^>]*name="([^"]+)"', xml)]
        elif ext in (".odt", ".odp", ".ods") and "content.xml" in names:
            xml = z.read("content.xml")[: MAX_READ * 4].decode("utf-8", "replace")
            paras = _xml_text(xml, r"<text:(?:h|p)[ >].*?</text:(?:h|p)>")
        info.excerpt = "\n".join(paras)[:600]
        if not info.title:
            for p in paras[:5]:
                t = _clean_title(p)
                if t:
                    info.title = t
                    break
    return info


def _pdf(path: str) -> ContentInfo:
    info = ContentInfo(rel_path="", kind="pdf")
    with open(long_path(path), "rb") as fh:
        head = fh.read(1024 * 1024)
    m = re.search(rb"/Title\s*\((.*?)(?<!\\)\)", head, re.S)
    if m:
        raw = m.group(1)
        if raw.startswith(b"\xfe\xff"):
            title = raw[2:].decode("utf-16-be", "replace")
        else:
            title = raw.decode("latin-1", "replace")
        info.title = _clean_title(title.replace("\\(", "(").replace("\\)", ")"))
    else:
        m = re.search(rb"/Title\s*<([0-9A-Fa-f\s]+)>", head)
        if m:
            try:
                data = bytes.fromhex(m.group(1).decode().replace(" ", ""))
                enc = "utf-16" if data.startswith((b"\xfe\xff", b"\xff\xfe")) else "latin-1"
                info.title = _clean_title(data.decode(enc, "replace"))
            except ValueError:
                pass
    return info


def _text(path: str, ext: str) -> ContentInfo:
    info = ContentInfo(rel_path="", kind="text")
    with open(long_path(path), "rb") as fh:
        text = _decode(fh.read(MAX_READ))
    info.excerpt = text[:600]
    if ext in (".java", ".kt"):
        pm = re.search(r"^\s*package\s+([\w.]+)\s*;?", text, re.M)
        if pm:
            info.java_package = pm.group(1)
        return info
    if ext in (".html", ".htm"):
        m = re.search(r"<title>(.*?)</title>", text, re.S | re.I) or re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I)
        if m:
            info.title = _clean_title(re.sub(r"<[^>]+>", "", m.group(1)))
        return info
    if ext in TEXT_EXTS - {".txt", ".md", ".markdown", ".rst", ".tex"}:
        return info  # Konfigurations-/Datendateien: keine Titel ableiten
    for line in text.splitlines()[:15]:
        t = _clean_title(line)
        if t:
            info.title = t
            break
    return info


def analyze_file(root: str, rel_path: str) -> ContentInfo:
    path = os.path.join(root, *rel_path.split("/"))
    ext = os.path.splitext(rel_path)[1].lower()
    try:
        if os.path.getsize(long_path(path)) > MAX_FILE_SIZE and ext not in (".pdf",):
            return ContentInfo(rel_path, error="Datei zu groß für die Inhaltsanalyse.")
        if ext in OFFICE_EXTS:
            info = _office(path, ext)
        elif ext == ".pdf":
            info = _pdf(path)
        elif ext in TEXT_EXTS:
            info = _text(path, ext)
        else:
            return ContentInfo(rel_path, error="Dateityp wird nicht unterstützt.")
    except zipfile.BadZipFile:
        return ContentInfo(rel_path, error="Datei ist beschädigt oder kein gültiges Office-Dokument.")
    except OSError as exc:
        from .scanner import explain_os_error

        return ContentInfo(rel_path, error=explain_os_error(exc))
    info.rel_path = rel_path
    return info


def analyze_contents(snapshot: Snapshot, rel_paths: list[str], progress=None, cancel=None) -> dict[str, ContentInfo]:
    out: dict[str, ContentInfo] = {}
    for i, rel in enumerate(rel_paths):
        if cancel is not None and cancel.is_set():
            break
        out[rel] = analyze_file(snapshot.root_path, rel)
        if progress:
            progress(i + 1, rel)
    return out
