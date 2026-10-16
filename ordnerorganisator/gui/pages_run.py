"""Schritt 6 (Ausführung) und Schritt 7 (Abschluss)."""

from __future__ import annotations

from html import escape

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
)

from ..executor import (
    PH_APPLYING,
    PH_COPIED,
    PH_COPYING,
    PH_CREATED,
    PH_DONE,
    PH_UNDOING,
    PH_UNDONE,
    PHASE_LABELS_DE,
    Executor,
    Progress,
    RunState,
)
from ..models import format_size
from ..paths import open_in_file_manager
from .base_page import Page
from .dialogs import mono_font
from .widgets import InfoBox, html_list, muted
from .workers import Worker


class ExecutePage(Page):
    title = "Schritt 6: Ausführung"
    subtitle = "Die Kopie wird erstellt und die genehmigten Änderungen werden ausschließlich darin umgesetzt."

    def __init__(self, win):
        super().__init__(win)
        self.worker: Worker | None = None
        self.info = InfoBox("", "info")
        self.layout_.addWidget(self.info)
        self.phase = QLabel("")
        f = self.phase.font()
        f.setBold(True)
        self.phase.setFont(f)
        self.layout_.addWidget(self.phase)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.layout_.addWidget(self.bar)
        self.current = muted("")
        self.layout_.addWidget(self.current)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono_font())
        self.layout_.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self.cancel)
        self.resume_btn = QPushButton("Fortsetzen")
        self.resume_btn.clicked.connect(self.start)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.resume_btn)
        row.addStretch(1)
        self.layout_.addLayout(row)

    def enter(self) -> None:
        st = self.session.run
        if st is None:
            return
        self.info.set_text(
            f"Original (wird nur gelesen): <b>{escape(st.source_root)}</b><br>"
            f"Kopie: <b>{escape(st.dest_root)}</b>", "info")
        if not self.busy() and st.phase in (PH_CREATED, PH_COPYING, PH_COPIED, PH_APPLYING, PH_UNDOING):
            self.start()

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def back_enabled(self) -> bool:
        return False

    def next_enabled(self) -> bool:
        st = self.session.run
        return not self.busy() and st is not None and st.phase in (PH_DONE, PH_UNDONE)

    def next_label(self) -> str:
        return "Zum Abschluss"

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def start(self) -> None:
        st = self.session.run
        if st is None or self.busy():
            return
        self.cancel_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self._append(f"▶ Start ({PHASE_LABELS_DE.get(st.phase, st.phase)})")

        def task(progress, cancel):
            exe = Executor(st, progress=progress, cancel=cancel)
            if st.phase in (PH_CREATED, PH_COPYING):
                errors = exe.copy_phase()
                if errors:
                    return "copy_errors"
            if st.phase in (PH_COPIED, PH_APPLYING):
                exe.apply_phase()
            elif st.phase == PH_UNDOING:
                exe.undo()
            return "done"

        self.worker = Worker(task, self)
        self.worker.progress.connect(self._progress)
        self.worker.succeeded.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.start()
        self.win.update_nav()

    def _progress(self, p: Progress) -> None:
        pct = p.done / p.total if p.total else 1.0
        self.bar.setValue(int(pct * 1000))
        if p.phase == "copy":
            self.phase.setText(f"1/2 Kopie wird erstellt … {pct * 100:.0f} % "
                               f"({format_size(p.done)} von {format_size(p.total)})")
        elif p.phase == "apply":
            self.phase.setText(f"2/2 Änderungen werden in der Kopie umgesetzt … {pct * 100:.0f} %")
        else:
            self.phase.setText(f"Änderungen werden rückgängig gemacht … {pct * 100:.0f} %")
        self.current.setText(p.current)

    def _finished(self, result: str) -> None:
        st = self.session.run
        self.cancel_btn.setEnabled(False)
        if result == "copy_errors":
            self._append(f"⚠ {len(st.copy_errors)} Datei(en) konnten nicht kopiert werden.")
            box = QMessageBox(self)
            box.setWindowTitle("Nicht alle Dateien kopiert")
            box.setText(f"{len(st.copy_errors)} Datei(en) konnten nicht kopiert werden:")
            box.setInformativeText("\n".join(f"{e['pfad']}: {e['fehler']}" for e in st.copy_errors[:12])
                                   + ("\n…" if len(st.copy_errors) > 12 else ""))
            retry = box.addButton("Erneut versuchen", QMessageBox.AcceptRole)
            cont = box.addButton("Ohne diese Dateien fortfahren", QMessageBox.ActionRole)
            box.addButton("Später fortsetzen", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                st.phase = PH_COPYING
                st.save()
                self.start()
            elif box.clickedButton() is cont:
                self.start()
            else:
                self.resume_btn.setEnabled(True)
                self.phase.setText("Pausiert – der Vorgang kann jederzeit fortgesetzt werden.")
            return
        self.bar.setValue(1000)
        self.phase.setText("Fertig.")
        self._append(f"✔ {PHASE_LABELS_DE.get(st.phase, st.phase)}")
        self.win.update_nav()
        self.win.go_next()

    def _failed(self, msg: str) -> None:
        self._append(f"✗ Fehler: {msg}")
        self.cancel_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        QMessageBox.warning(self, "Fehler", f"{msg}\n\nDer bisherige Stand ist gespeichert. Sie können den Vorgang "
                                           "über „Fortsetzen“ erneut starten.")
        self.win.update_nav()

    def _cancelled(self) -> None:
        self._append("■ Abgebrochen – der Stand wurde gespeichert.")
        self.phase.setText("Abgebrochen. Der Vorgang kann später fortgesetzt werden (auch nach einem Neustart).")
        self.cancel_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.win.update_nav()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()


class FinishPage(Page):
    title = "Schritt 7: Abschluss"
    subtitle = ""

    def __init__(self, win):
        super().__init__(win)
        self.result = InfoBox("", "ok")
        self.layout_.addWidget(self.result)
        self.problems = InfoBox("", "warn")
        self.layout_.addWidget(self.problems)
        row = QHBoxLayout()
        self.open_copy = QPushButton("Kopie im Explorer öffnen")
        self.open_copy.clicked.connect(lambda: self.session.run and open_in_file_manager(self.session.run.dest_root))
        self.open_log = QPushButton("Änderungsprotokoll öffnen")
        self.open_log.clicked.connect(lambda: self.session.run and open_in_file_manager(self.session.run.report_file))
        self.open_dir = QPushButton("Protokollordner öffnen")
        self.open_dir.clicked.connect(lambda: self.session.run and open_in_file_manager(self.session.run.run_dir))
        self.undo_btn = QPushButton("Änderungen in der Kopie rückgängig machen")
        self.undo_btn.clicked.connect(self.undo)
        for b in (self.open_copy, self.open_log, self.open_dir, self.undo_btn):
            row.addWidget(b)
        row.addStretch(1)
        self.layout_.addLayout(row)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(mono_font())
        self.layout_.addWidget(self.report, 1)
        self.worker: Worker | None = None

    def enter(self) -> None:
        st: RunState | None = self.session.run
        if st is None:
            return
        stats = st.stats
        if st.phase == PH_UNDONE:
            head = "<b>Alle Änderungen in der Kopie wurden rückgängig gemacht.</b>"
        else:
            head = "<b>Fertig! Die organisierte Kopie wurde erstellt.</b>"
        self.result.set_text(
            f"{head}<br>Kopie: <b>{escape(st.dest_root)}</b><br>"
            f"Original: {escape(st.source_root)} – <b>unverändert</b>.<br>"
            f"Kopierte Dateien: {stats.get('kopiert', 0) + stats.get('bereits_vorhanden', 0)} · "
            f"verschoben/umbenannt: {stats.get('verschoben', 0)} · fehlgeschlagen: {stats.get('fehlgeschlagen', 0)}<br>"
            f"Änderungsprotokoll: {escape(str(st.report_file))}", "ok")
        problems = [f"Kopierfehler – {e['pfad']}: {e['fehler']}" for e in st.copy_errors]
        problems += [f"{e['pfad']}: {e['fehler']}" for e in st.apply_errors]
        problems += st.notes
        self.problems.setVisible(bool(problems))
        self.problems.set_text("<b>Bitte beachten:</b>" + html_list(problems, 15))
        self.undo_btn.setEnabled(st.phase == PH_DONE)
        try:
            self.report.setPlainText(st.report_file.read_text(encoding="utf-8"))
        except OSError:
            self.report.setPlainText("")

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def back_enabled(self) -> bool:
        return False

    def next_label(self) -> str:
        return "Neuen Ordner organisieren"

    def on_next(self) -> None:
        self.win.new_session()

    def undo(self) -> None:
        st = self.session.run
        if st is None or st.phase != PH_DONE:
            return
        if QMessageBox.question(
            self, "Rückgängig machen?",
            "Alle Verschiebungen und Umbenennungen in der Kopie werden zurückgenommen. Die Kopie hat danach wieder "
            "die ursprüngliche Struktur. Das Original ist davon nicht betroffen.\n\nFortfahren?",
        ) != QMessageBox.Yes:
            return
        self.undo_btn.setEnabled(False)

        def task(progress, cancel):
            Executor(st, progress=progress, cancel=cancel).undo()
            return True

        self.worker = Worker(task, self)
        self.worker.succeeded.connect(lambda _r: self.enter())
        self.worker.failed.connect(lambda msg: (QMessageBox.warning(self, "Fehler", msg), self.enter()))
        self.worker.cancelled.connect(self.enter)
        self.worker.start()
