"""Durchläuft den kompletten Assistenten headless (Qt „offscreen“)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ordnerorganisator.secrets_store import SecretStore, _MemoryBackend  # noqa: E402
from ordnerorganisator.settings import AppSettings  # noqa: E402

from conftest import snapshot_dirs, snapshot_files  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("Zeitüberschreitung beim Warten auf die Oberfläche")


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from ordnerorganisator.gui import dialogs, pages_review
    from ordnerorganisator.gui.main_window import MainWindow

    # Modale Dialoge automatisch bestätigen
    def confirm_exec(self):
        self.check.setChecked(True)
        return 1

    monkeypatch.setattr(dialogs.ConfirmExecutionDialog, "exec", confirm_exec)
    monkeypatch.setattr(pages_review.ConfirmExecutionDialog, "exec", confirm_exec)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: pytest.fail(f"Warnung: {a[2:]}")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    desk = tmp_path / "Desktop"
    desk.mkdir()
    settings = AppSettings()
    settings.output_dir = str(desk)
    win = MainWindow(settings, SecretStore(backend=_MemoryBackend()))
    win.show()
    yield win, desk
    win.close()


def test_full_wizard_flow(window, example_project):
    win, desk = window
    before = snapshot_files(example_project)

    start = win.pages[0]
    start.path.setCurrentText(str(example_project))
    assert win.next_btn.isEnabled()
    win.next_btn.click()
    wait_until(lambda: win.stack.currentIndex() == 1)
    assert win.session.snapshot.file_count == 5
    structure = win.pages[1]
    assert "Allgemeine Dateiablage" in structure.summary.label.text()
    structure.tree.search.setText("urlaub")
    assert structure.tree.proxy.rowCount() == 1  # nur „Bilder“ bleibt sichtbar

    win.next_btn.click()  # → KI-Auswahl
    assert win.stack.currentIndex() == 2
    ai = win.pages[2]
    assert ai.provider_radios["builtin"].isChecked()
    assert win.next_btn.text() == "Analyse starten"
    win.next_btn.click()
    wait_until(lambda: win.stack.currentIndex() == 3)

    result = win.pages[3]
    assert result.table.rowCount() == len(win.session.plan.operations) > 0
    assert "Integrierte KI" in result.header.label.text()
    win.next_btn.click()  # → Vergleich
    assert win.stack.currentIndex() == 4
    review = win.pages[4]
    assert win.next_btn.isEnabled()

    # Eine Änderung ablehnen und ein Ziel bearbeiten
    plan = win.session.plan
    row_of = {review.table.item(r, 0).data(Qt.UserRole): r for r in range(review.table.rowCount())}
    rename_bilder = next(op for op in plan.operations if op.source == "Bilder/urlaub.jpg")
    review.table.item(row_of[rename_bilder.id], 0).setCheckState(Qt.Unchecked)
    move_pdf = next(op for op in plan.operations if op.source == "final_final_v2.pdf")
    review.table.item(row_of[move_pdf.id], 3).setText("Dokumente/Abschlussbericht.pdf")
    review.revalidate()
    assert not rename_bilder.accepted
    assert move_pdf.target == "Dokumente/Abschlussbericht.pdf"
    assert not win.session.resolution.has_errors

    # Konflikt erzeugen → „Kopie erstellen“ gesperrt
    review.table.item(row_of[move_pdf.id], 3).setText("Dokumente/datei1.txt")
    review.revalidate()
    assert win.session.resolution.has_errors and not win.next_btn.isEnabled()
    review.table.item(row_of[move_pdf.id], 3).setText("Dokumente/Abschlussbericht.pdf")
    review.revalidate()
    assert win.next_btn.isEnabled()

    win.next_btn.click()  # Bestätigung (automatisch) → Ausführung
    wait_until(lambda: win.stack.currentIndex() == 6, timeout=30)
    run = win.session.run
    dest = Path(run.dest_root)
    assert dest.parent == desk
    files = snapshot_files(dest)
    assert "Dokumente/Abschlussbericht.pdf" in files
    assert "Bilder/urlaub.jpg" in files  # abgelehnte Umbenennung wurde nicht ausgeführt
    assert "Downloads_Neu/setup.exe" in files
    assert snapshot_files(example_project) == before  # Original unverändert

    finish = win.pages[6]
    assert "Fertig" in finish.result.label.text()
    assert "op" in finish.report.toPlainText()
    finish.undo()
    wait_until(lambda: not finish.busy() and run.phase == "undone")
    assert snapshot_files(dest) == before
    assert "Downloads neu" in snapshot_dirs(dest)

    win.next_btn.click()  # neuer Vorgang
    assert win.stack.currentIndex() == 0 and win.session.snapshot is None


def test_exchange_import_flow(window, example_project, monkeypatch):
    win, _desk = window
    from ordnerorganisator.gui import page_ai

    win.pages[0].path.setCurrentText(str(example_project))
    win.next_btn.click()
    wait_until(lambda: win.stack.currentIndex() == 1)
    win.next_btn.click()
    ai = win.pages[2]
    ai.provider_radios["exchange"].setChecked(True)
    assert not win.next_btn.isEnabled()  # erst nach dem Import

    answer = """```json
    {"operations": [
      {"type": "move", "source": "datei1.txt", "target": "Notizen/datei1.txt", "reason": "Notiz"},
      {"type": "delete", "source": "Bilder"}
    ]}
    ```"""

    class FakePaste:
        def __init__(self, *_a, **_k):
            pass

        def exec(self):
            return 1

        def text(self):
            return answer

    monkeypatch.setattr(page_ai, "PasteAnswerDialog", FakePaste)
    ai._paste_answer()
    assert win.stack.currentIndex() == 3
    assert list(win.invalid_messages) == ["op2"]
    assert "nicht erlaubt" in win.pages[3].invalid.label.text()
    win.next_btn.click()
    assert win.next_btn.isEnabled()


def test_package_preview_and_copy(window, example_project, monkeypatch):
    win, _desk = window
    from ordnerorganisator.gui import dialogs

    win.pages[0].path.setCurrentText(str(example_project))
    win.next_btn.click()
    wait_until(lambda: win.stack.currentIndex() == 1)
    win.next_btn.click()
    ai = win.pages[2]
    shown = []
    monkeypatch.setattr(dialogs.PackagePreviewDialog, "exec", lambda self: shown.append(self) or 1)
    ai._preview(for_chat=True)
    assert shown and "Downloads neu/setup.exe" in shown[0].packages[0].text
    ai._copy_package()
    assert "Ordnerstruktur" in QApplication.clipboard().text()
