"""Schritt 1 (Ordner auswählen) und Schritt 2 (Strukturansicht)."""

from __future__ import annotations

import os
from collections import Counter
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ai.projecttypes import detect_project_type
from ..executor import PHASE_LABELS_DE, list_unfinished_runs
from ..filetypes import category_by_key
from ..models import format_size
from ..paths import desktop_dir
from ..scanner import scan_folder
from .base_page import Page
from .widgets import InfoBox, SearchableTree, build_snapshot_model, html_list, muted
from .workers import Worker


def _risky_folder(path: str) -> str | None:
    p = os.path.abspath(path)
    norm = os.path.normcase(p)
    drive_root = os.path.splitdrive(p)[0] + os.sep
    if os.path.normcase(drive_root) == norm or p == os.sep:
        return "Sie haben ein ganzes Laufwerk gewählt. Das kann sehr lange dauern und viel Speicher benötigen."
    for env in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        val = os.environ.get(env)
        if val and (norm == os.path.normcase(val) or norm.startswith(os.path.normcase(val) + os.sep)):
            return "Dies ist ein System- bzw. Programmordner. Eine Umorganisation ist dort nicht sinnvoll."
    return None


class StartPage(Page):
    title = "Schritt 1: Ordner auswählen"
    subtitle = "Wählen Sie den Ordner, den die KI analysieren und neu organisieren soll."

    def __init__(self, win):
        super().__init__(win)
        self.worker: Worker | None = None
        self.layout_.addWidget(InfoBox(
            "<b>Ihr Original ist sicher.</b> Der gewählte Ordner wird nur gelesen und niemals verändert oder "
            "gelöscht. Erst nachdem Sie die Vorschläge geprüft und bestätigt haben, entsteht eine <b>Kopie auf dem "
            "Desktop</b> – und nur diese wird organisiert.", "ok"))

        row = QHBoxLayout()
        self.path = QComboBox()
        self.path.setEditable(True)
        self.path.lineEdit().setPlaceholderText(r"Ordnerpfad eingeben, z. B. C:\Users\Name\Documents\MeinProjekt")
        self.path.addItems(self.session.settings.recent_folders)
        self.path.setCurrentText("")
        self.path.lineEdit().returnPressed.connect(self.on_next)
        self.path.currentTextChanged.connect(lambda _t: self.win.update_nav())
        browse = QPushButton("Durchsuchen …")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.layout_.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = muted("")
        self.layout_.addWidget(self.progress)
        self.layout_.addWidget(self.status)

        self.resume_box = QWidget()
        rb = QVBoxLayout(self.resume_box)
        rb.setContentsMargins(0, 8, 0, 0)
        rb.addWidget(QLabel("<b>Unterbrochene Vorgänge</b> – können fortgesetzt werden:"))
        self.resume_list = QListWidget()
        self.resume_list.setMaximumHeight(130)
        rb.addWidget(self.resume_list)
        rbtn = QPushButton("Ausgewählten Vorgang fortsetzen")
        rbtn.clicked.connect(self.resume_selected)
        rb.addWidget(rbtn, 0, Qt.AlignLeft)
        self.layout_.addWidget(self.resume_box)
        self.layout_.addStretch(1)

    def enter(self) -> None:
        self.resume_list.clear()
        runs = list_unfinished_runs()
        for r in runs:
            it = QListWidgetItem(f"{r.created_at.replace('T', ' ')} – {PHASE_LABELS_DE.get(r.phase, r.phase)} – "
                                 f"{r.source_root} → {r.dest_root}")
            it.setData(Qt.UserRole, r.run_id)
            self.resume_list.addItem(it)
        self.resume_box.setVisible(bool(runs))
        if runs:
            self.resume_list.setCurrentRow(0)

    def browse(self) -> None:
        start = self.path.currentText().strip() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Ordner auswählen", start)
        if chosen:
            self.path.setCurrentText(os.path.normpath(chosen))

    def next_label(self) -> str:
        return "Ordner einlesen"

    def next_enabled(self) -> bool:
        return bool(self.path.currentText().strip()) and not self.busy()

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def on_next(self) -> None:
        if self.busy():
            return
        path = os.path.expanduser(self.path.currentText().strip().strip('"'))
        if not path:
            return
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Ordner nicht gefunden", f"„{path}“ existiert nicht oder ist kein Ordner.")
            return
        risk = _risky_folder(path)
        if risk and QMessageBox.question(self, "Wirklich diesen Ordner?", f"{risk}\n\nTrotzdem fortfahren?") != \
                QMessageBox.Yes:
            return
        dest_parent = self.session.settings.output_dir or str(desktop_dir())
        dn, pn = os.path.normcase(os.path.abspath(dest_parent)), os.path.normcase(os.path.abspath(path))
        if dn == pn or dn.startswith(pn + os.sep):
            QMessageBox.warning(
                self, "Zielordner liegt im gewählten Ordner",
                f"Die Kopie würde unter „{dest_parent}“ entstehen – das liegt innerhalb des gewählten Ordners.\n\n"
                "Sie können fortfahren und vor dem Kopieren in der KI-Auswahl einen anderen Zielordner wählen.",
            )
        self.progress.show()
        self.status.setText("Ordner wird eingelesen …")

        def task(progress, cancel):
            return scan_folder(path, progress=lambda n, rel: progress((n, rel)), cancel=cancel)

        self.worker = Worker(task, self)
        self.worker.progress.connect(lambda p: self.status.setText(f"{p[0]} Einträge gelesen … {p[1]}"))
        self.worker.succeeded.connect(lambda snap: self._scanned(path, snap))
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(lambda: self._failed("Einlesen abgebrochen."))
        self.worker.start()
        self.win.update_nav()

    def _scanned(self, path: str, snap) -> None:
        self.progress.hide()
        self.status.setText("")
        self.session.reset()
        self.session.snapshot = snap
        self.session.settings.remember_folder(os.path.abspath(path))
        self.session.settings.save()
        self.win.go_next()

    def _failed(self, msg: str) -> None:
        self.progress.hide()
        self.status.setText("")
        QMessageBox.warning(self, "Einlesen nicht möglich", msg)
        self.win.update_nav()

    def resume_selected(self) -> None:
        it = self.resume_list.currentItem()
        if it is not None:
            self.win.resume_run(it.data(Qt.UserRole))


class StructurePage(Page):
    title = "Schritt 2: Bestehende Struktur"
    subtitle = "So ist der Ordner aktuell aufgebaut. Durchsuchen Sie die Struktur nach Namen, Typen oder Pfaden."

    def __init__(self, win):
        super().__init__(win)
        self.summary = InfoBox("", "info")
        self.layout_.addWidget(self.summary)
        self.show_hidden = QCheckBox("Versteckte und Systemdateien anzeigen")
        self.show_hidden.setChecked(self.session.settings.show_hidden_in_tree)
        self.show_hidden.toggled.connect(self._rebuild)
        self.layout_.addWidget(self.show_hidden)
        self.tree = SearchableTree()
        self.layout_.addWidget(self.tree, 1)

    def enter(self) -> None:
        snap = self.session.snapshot
        if snap is None:
            return
        det = detect_project_type(snap)
        cats = Counter(e.category for e in snap.files() if not e.is_special)
        top = ", ".join(f"{n}× {category_by_key(k).label('de')}" for k, n in cats.most_common(6))
        hidden = sum(1 for e in snap.root.iter_tree() if e.is_special)
        text = (
            f"<b>{escape(snap.root_path)}</b><br>"
            f"{snap.dir_count} Ordner · {snap.file_count} Dateien · {format_size(snap.total_size)}"
            f"{f' · {hidden} versteckt/System' if hidden else ''}<br>"
            f"Dateitypen: {escape(top) or '–'}<br>"
            f"Erkannte Projektart: <b>{escape(det.label)}</b> ({escape('; '.join(det.reasons))})"
        )
        if snap.skipped:
            text += "<br><br><b>Hinweise:</b>" + html_list([f"{p}: {m}" for p, m in snap.skipped], 8)
        self.summary.set_text(text, "warn" if snap.skipped else "info")
        self._rebuild()

    def _rebuild(self) -> None:
        if self.session.snapshot is None:
            return
        self.session.settings.show_hidden_in_tree = self.show_hidden.isChecked()
        self.tree.set_model(build_snapshot_model(self.session.snapshot, self.show_hidden.isChecked()), 0)
        self.tree.view.setColumnWidth(1, 200)
        self.tree.view.setColumnWidth(2, 90)

    def next_label(self) -> str:
        return "Weiter zur KI-Auswahl"
