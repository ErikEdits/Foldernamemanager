from __future__ import annotations

import zipfile

from ordnerorganisator.ai.base import AnalysisRequest
from ordnerorganisator.ai.engine import BuiltinEngine
from ordnerorganisator.ai.naming import (
    detect_language,
    extract_date,
    is_generic_name,
    style_name,
    suggest_name,
)
from ordnerorganisator.content import analyze_contents, analyze_file
from ordnerorganisator.planner import resolve_plan
from ordnerorganisator.scanner import scan_folder
from ordnerorganisator.settings import UserRules

from conftest import make_tree


def analyze(root, contents=None, **rules):
    snap = scan_folder(root)
    plan = BuiltinEngine().analyze(AnalysisRequest(snap, UserRules(**rules), contents=contents or {}))
    return snap, plan


def targets(plan):
    return {op.source: op.target for op in plan.operations}


# -- Namensregeln -----------------------------------------------------------

def test_suggest_name_examples():
    r = UserRules()
    assert suggest_name("final_final_v2.pdf", is_dir=False, rules=r, lang="de").new_name == "Final_v2.pdf"
    assert suggest_name("Downloads neu", is_dir=True, rules=r, lang="de").new_name == "Downloads_Neu"
    assert suggest_name("Meeting 12.03.2024 Notizen.txt", is_dir=False, rules=r, lang="de").new_name == (
        "Meeting_2024-03-12_Notizen.txt"
    )
    assert suggest_name("rechnung (1).PDF", is_dir=False, rules=r, lang="de").new_name == "Rechnung.pdf"
    # Kamera-Namen behalten ihre Schreibweise, nur die Endung wird angeglichen
    assert suggest_name("IMG_20230715_101500.JPG", is_dir=False, rules=r, lang="de").new_name == (
        "IMG_20230715_101500.jpg"
    )
    assert suggest_name("_Wichtig", is_dir=True, rules=r, lang="de") is None
    assert suggest_name("README.md", is_dir=False, rules=r, lang="de") is None
    # mehrdeutiges Datum bleibt unverändert
    c = suggest_name("03-04-2024 bericht.txt", is_dir=False, rules=r, lang="de")
    assert c.new_name == "03-04-2024_Bericht.txt" and c.ambiguous_dates == ["03-04-2024"]


def test_styles_and_translation():
    assert suggest_name("Meine Bilder", is_dir=True, rules=UserRules(naming_style="kebab"), lang="de").new_name == (
        "meine-bilder"
    )
    assert suggest_name("Bilder", is_dir=True, rules=UserRules(), lang="en", translate=True).new_name == "Images"
    # Unbekannte Wörter → keine halb übersetzten Namen
    assert suggest_name("Bilder Italien", is_dir=True, rules=UserRules(), lang="en", translate=True).new_name == (
        "Bilder_Italien"
    )
    assert style_name("3D-Modelle", "snake") == "3d_modelle"
    c = suggest_name("Projekt 12.03.2024", is_dir=True, rules=UserRules(date_format="german", naming_style="keep"), lang="de")
    assert c is None  # bereits im gewünschten Format


def test_generic_and_language_detection():
    assert is_generic_name("datei1.txt")
    assert is_generic_name("Neues Textdokument (2).txt")
    assert is_generic_name("Neuer Ordner", is_dir=True)
    assert not is_generic_name("Steuererklärung 2023.pdf")
    assert detect_language(["Bilder", "Rechnungen", "Downloads"]) == "de"
    assert detect_language(["Images", "Invoices"]) == "en"
    assert detect_language(["Downloads", "Videos"]) is None
    assert str(extract_date("IMG_20230715_101500.jpg")) == "2023-07-15"
    assert str(extract_date("WhatsApp Image 2023-07-15 at 10.15.00.jpeg")) == "2023-07-15"
    assert extract_date("foto.jpg") is None


# -- Integrierte KI ---------------------------------------------------------

def test_spec_example(example_project):
    snap, plan = analyze(example_project)
    t = targets(plan)
    assert t["Downloads neu"] == "Downloads_Neu"
    assert t["datei1.txt"] == "Dokumente/datei1.txt"  # Name wird nicht geraten
    assert t["final_final_v2.pdf"] == "Dokumente/Final_v2.pdf"
    assert any("nichtssagende Namen" in q for q in plan.questions)
    assert not resolve_plan(snap, plan).has_errors
    assert all(op.reason for op in plan.operations)


def test_existing_category_folder_is_reused_and_translated(tmp_path):
    root = make_tree(tmp_path / "R", {"Images": {"x.jpg": "1"}, "foto1.jpg": "1", "Rechnungen": {}, "Verträge": {}})
    _snap, plan = analyze(root)  # Sprache: Deutsch (Rechnungen)
    t = targets(plan)
    assert t["Images"] == "Bilder"
    assert t["foto1.jpg"] == "Bilder/foto1.jpg"


def test_no_grouping_for_dominant_category(tmp_path):
    root = make_tree(tmp_path / "R", {"Urlaub": {**{f"strand{i}.jpg": "1" for i in range(10)}, "clip.mp4": "1"}})
    _snap, plan = analyze(root)
    assert not any(op.target.startswith("Urlaub/Bilder") for op in plan.operations)
    assert not any(op.target.startswith("Urlaub/Videos") for op in plan.operations)


def test_code_projects_are_protected(tmp_path):
    root = make_tree(
        tmp_path / "R",
        {"MyApp": {"package.json": "{}", "src": {"index.js": "1"}, "Notes 1.2.2024.txt": "x"}, "a.pdf": "1"},
    )
    _snap, plan = analyze(root)
    assert not any(op.source and op.source.startswith("MyApp") for op in plan.operations)
    assert any("MyApp" in w for w in plan.warnings)


def test_copy_marker_kept_when_original_exists(tmp_path):
    root = make_tree(tmp_path / "R", {"Bericht.docx": "1", "Bericht - Kopie.docx": "2", "x.xlsx": "1", "y.pptx": "1"})
    snap, plan = analyze(root)
    t = targets(plan)
    assert t["Bericht.docx"] == "Dokumente/Bericht.docx"
    assert t["Bericht - Kopie.docx"] == "Dokumente/Bericht_Kopie.docx"
    assert not resolve_plan(snap, plan).has_errors


def test_photo_mode(tmp_path):
    spec = {f"DSC{i:04d}.jpg": "1" for i in range(10)}
    spec.update({"IMG_20230715_101500.jpg": "1", "IMG_20230715_101500.xmp": "1", "Urlaub": {"a.jpg": "1"}})
    snap, plan = analyze(root := make_tree(tmp_path / "R", spec))
    assert plan.project_type == "photos"
    t = targets(plan)
    assert t["IMG_20230715_101500.jpg"] == "2023/2023-07/IMG_20230715_101500.jpg"
    assert t["IMG_20230715_101500.xmp"] == "2023/2023-07/IMG_20230715_101500.xmp"
    assert "Urlaub/a.jpg" not in t
    assert any("kein Datum" in q for q in plan.questions)
    assert root


def test_java_and_minecraft_mode(tmp_path):
    root = make_tree(
        tmp_path / "R",
        {"pom.xml": "<p/>", "src": {"main": {"java": {"A.java": "x"}}}, "Anleitung.pdf": "1",
         "README.md": "r", "plugin.yml": "name: x", "server.jar": "1"},
    )
    _snap, plan = analyze(root)
    assert plan.project_type == "minecraft"
    t = targets(plan)
    assert t["Anleitung.pdf"] == "docs/Anleitung.pdf"
    assert t["plugin.yml"] == "src/main/resources/plugin.yml"
    assert "server.jar" not in t
    assert not any(op.source and op.source.startswith("src/") for op in plan.operations)


def test_java_files_without_build_use_content(tmp_path):
    root = make_tree(tmp_path / "R", {"Main.java": "package de.erik.app;\nclass Main {}", "Util.java": "class U{}",
                                       "Other.java": "package x;"})
    snap = scan_folder(root)
    contents = analyze_contents(snap, ["Main.java", "Util.java", "Other.java"])
    plan = BuiltinEngine().analyze(
        AnalysisRequest(snap, UserRules(project_mode="java"), contents=contents)
    )
    t = targets(plan)
    assert t["Main.java"] == "src/main/java/de/erik/app/Main.java"
    assert "Util.java" not in t
    assert any("Inhaltsanalyse" in q for q in plan.questions)


def test_video_mode(tmp_path):
    root = make_tree(
        tmp_path / "R",
        {"projekt.prproj": "1", "clip1.mp4": "1", "final_export.mp4": "1", "musik.mp3": "1", "Footage": {"c.mp4": "1"}},
    )
    snap, plan = analyze(root)
    assert plan.project_type == "video"
    t = targets(plan)
    assert t["Footage"] == "02_Videomaterial"
    assert t["clip1.mp4"] == "02_Videomaterial/clip1.mp4"
    assert t["final_export.mp4"] == "05_Export/final_export.mp4"
    assert t["projekt.prproj"] == "01_Projektdateien/projekt.prproj"
    assert any("neu verknüpft" in w for w in plan.warnings)
    assert not resolve_plan(snap, plan).has_errors


def test_english_and_kebab(tmp_path):
    root = make_tree(tmp_path / "R", {"Bilder": {"a.jpg": "1"}, "Notizen alt": {}, "b.pdf": "1", "c.png": "1"})
    _snap, plan = analyze(root, language="en", naming_style="kebab")
    t = targets(plan)
    assert t["Bilder"] == "images"
    assert t["Notizen alt"] == "notes-old"
    assert t["c.png"] == "images/c.png"
    assert t["b.pdf"] == "documents/b.pdf"


def test_low_confidence_not_preselected(tmp_path):
    root = make_tree(tmp_path / "R", {"lib.jar": "1", "Main.java": "class M {}"})
    _snap, plan = analyze(root, project_mode="java")
    jar = next(op for op in plan.operations if op.source == "lib.jar")
    assert jar.target == "libs/lib.jar" and not jar.accepted


# -- Inhaltsanalyse ----------------------------------------------------------

def test_content_analysis_titles(tmp_path):
    root = make_tree(tmp_path / "R", {"datei1.txt": "\n# Projektnotizen\nErste Idee", "x.csv": "a,b"})
    with zipfile.ZipFile(root / "Dokument1.docx", "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Mietvertrag Wohnung</w:t></w:r></w:p></w:body></w:document>")
    (root / "scan.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Title (Steuerbescheid 2023) >> endobj")
    assert analyze_file(str(root), "datei1.txt").title == "Projektnotizen"
    assert analyze_file(str(root), "Dokument1.docx").title == "Mietvertrag Wohnung"
    assert analyze_file(str(root), "scan.pdf").title == "Steuerbescheid 2023"

    snap = scan_folder(root)
    contents = analyze_contents(snap, ["datei1.txt", "Dokument1.docx", "scan.pdf"])
    plan = BuiltinEngine().analyze(AnalysisRequest(snap, UserRules(group_by_type=False), contents=contents))
    t = targets(plan)
    assert t["datei1.txt"] == "Projektnotizen.txt"
    assert t["Dokument1.docx"] == "Mietvertrag_Wohnung.docx"
    assert t["scan.pdf"] == "Steuerbescheid_2023.pdf"
    op = next(o for o in plan.operations if o.source == "datei1.txt")
    assert "Inhalt" in op.reason and op.confidence == 0.5
