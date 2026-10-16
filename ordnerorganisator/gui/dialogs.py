"""Dialoge: Paketvorschau, Antwort einfügen, Inhaltsanalyse, Bestätigung."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from ..ai.exchange import export_packages
from ..ai.prompt import AnalysisPackage, PrivacySummary
from ..content import suggested_candidates, supports
from ..executor import PreparedRun
from ..models import Snapshot, format_size
from ..paths import desktop_dir, open_in_file_manager
from ..redaction import redact
from .widgets import InfoBox, heading, html_list, muted


def mono_font():
    return QFontDatabase.systemFont(QFontDatabase.FixedFont)


class PackagePreviewDialog(QDialog):
    """Zeigt genau, was weitergegeben würde – mit Kopier- und Speicherfunktion."""

    def __init__(self, packages: list[AnalysisPackage], privacy: PrivacySummary, root_name: str,
                 parent=None, allow_export: bool = True, target_desc: str = "die Cloud-KI"):
        super().__init__(parent)
        self.setWindowTitle("Analysepaket prüfen")
        self.resize(900, 680)
        self.packages = packages
        self.root_name = root_name
        self.saved_files: list = []
        lay = QVBoxLayout(self)
        lay.addWidget(heading(f"Diese Informationen erhält {target_desc}", 13))
        lay.addWidget(InfoBox(html_list(privacy.lines()), "info"))
        lay.addWidget(muted(
            "Dateinamen und Pfade können persönliche Informationen enthalten. Prüfen Sie den Text unten, "
            "bevor Sie ihn weitergeben. API-Keys und Passwörter werden automatisch entfernt."
        ))
        self.tabs = QTabWidget()
        for pkg in packages:
            edit = QPlainTextEdit(pkg.text)
            edit.setReadOnly(True)
            edit.setFont(mono_font())
            self.tabs.addTab(edit, pkg.title)
        lay.addWidget(self.tabs, 1)
        if len(packages) > 1:
            lay.addWidget(InfoBox(
                f"Der Ordner ist groß – das Paket wurde in <b>{len(packages)} Teile</b> aufgeteilt. Geben Sie die "
                "Teile nacheinander an die KI und importieren Sie anschließend alle Antworten gemeinsam.", "warn"))
        buttons = QHBoxLayout()
        if allow_export:
            self.copy_btn = QPushButton("Angezeigten Teil in die Zwischenablage kopieren")
            self.copy_btn.clicked.connect(self.copy_current)
            self.save_btn = QPushButton("Als Datei auf dem Desktop speichern")
            self.save_btn.clicked.connect(self.save_files)
            buttons.addWidget(self.copy_btn)
            buttons.addWidget(self.save_btn)
        buttons.addStretch(1)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        lay.addLayout(buttons)

    def copy_current(self) -> None:
        pkg = self.packages[self.tabs.currentIndex()]
        QGuiApplication.clipboard().setText(redact(pkg.text))
        QMessageBox.information(
            self, "Kopiert",
            f"{pkg.title} wurde in die Zwischenablage kopiert.\n\nFügen Sie den Text in ChatGPT oder eine andere "
            "KI ein. Speichern Sie die JSON-Antwort anschließend als Datei oder fügen Sie sie über "
            "„Antwort einfügen“ ein.",
        )

    def save_files(self) -> None:
        try:
            self.saved_files = export_packages(self.packages, self.root_name, desktop_dir())
        except OSError as exc:
            QMessageBox.warning(self, "Speichern fehlgeschlagen", str(exc))
            return
        names = "\n".join(str(p) for p in self.saved_files)
        box = QMessageBox(self)
        box.setWindowTitle("Gespeichert")
        box.setText(f"Das Analysepaket wurde gespeichert:\n\n{names}\n\nLaden Sie die Datei bei ChatGPT o. Ä. hoch.")
        open_btn = box.addButton("Ordner öffnen", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            open_in_file_manager(self.saved_files[0].parent)


class PasteAnswerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Antwort der KI einfügen")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Antwort der Cloud-KI einfügen", 13))
        lay.addWidget(muted(
            "Fügen Sie die komplette Antwort ein. Erklärender Text rund um den JSON-Codeblock wird automatisch "
            "ignoriert. Mehrere Teilantworten können nacheinander eingefügt werden."
        ))
        self.edit = QPlainTextEdit()
        self.edit.setFont(mono_font())
        self.edit.setPlaceholderText('{\n  "operations": [ … ]\n}')
        lay.addWidget(self.edit, 1)
        paste = QPushButton("Aus Zwischenablage einfügen")
        paste.clicked.connect(lambda: self.edit.setPlainText(QGuiApplication.clipboard().text()))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Plan übernehmen")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(paste)
        row.addStretch(1)
        row.addWidget(bb)
        lay.addLayout(row)

    def text(self) -> str:
        return self.edit.toPlainText()


class ContentSelectionDialog(QDialog):
    """Auswahl der Dateien, deren Inhalt gelesen werden darf."""

    def __init__(self, snapshot: Snapshot, selected: list[str], target_desc: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inhaltsanalyse – Dateien auswählen")
        self.resize(780, 600)
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Welche Dateien dürfen inhaltlich gelesen werden?", 13))
        lay.addWidget(InfoBox(
            "Gelesen werden nur kleine Ausschnitte: Titel/Überschrift, erste Zeilen bzw. bei Java-Dateien die "
            f"Paketangabe. <b>Verwendung: {escape(target_desc)}</b>. Dateien werden dabei nicht verändert.",
            "warn" if "übertragen" in target_desc else "info",
        ))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtern …")
        self.filter.setClearButtonEnabled(True)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        candidates = set(suggested_candidates(snapshot))
        chosen = set(selected) if selected else candidates
        files = [e for e in snapshot.files() if supports(e.ext) and not e.is_special and not e.is_link
                 and not snapshot.is_protected(e.rel_path)]
        files.sort(key=lambda e: (e.rel_path not in candidates, e.rel_path.casefold()))
        for e in files:
            label = e.rel_path + ("   (empfohlen: nichtssagender Name)" if e.rel_path in candidates else "")
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, e.rel_path)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if e.rel_path in chosen else Qt.Unchecked)
            self.list.addItem(it)
        self.filter.textChanged.connect(self._apply_filter)
        row = QHBoxLayout()
        for text, state in (("Alle sichtbaren auswählen", Qt.Checked), ("Keine", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._set_all(s))
            row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        if not files:
            lay.insertWidget(2, InfoBox("Keine unterstützten Dateien gefunden (Text, Office, PDF, Java).", "warn"))

    def _apply_filter(self, text: str) -> None:
        t = text.strip().casefold()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().casefold())

    def _set_all(self, state) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden():
                it.setCheckState(state)

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class ConfirmExecutionDialog(QDialog):
    def __init__(self, prep: PreparedRun, counts: dict[str, int], n_ops: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kopie erstellen – Bestätigung")
        self.resize(680, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Bitte bestätigen", 14))
        form = QFormLayout()
        form.addRow("Original (bleibt unverändert):", self._sel(prep.source_root))
        form.addRow("Kopie wird erstellt unter:", self._sel(prep.dest_root))
        form.addRow("Dateien:", QLabel(f"{prep.file_count} ({format_size(prep.required_bytes)})"))
        form.addRow("Freier Speicher am Ziel:", QLabel(format_size(prep.free_bytes)))
        form.addRow("Genehmigte Änderungen:", QLabel(
            f"{n_ops} Anweisungen – {counts.get('renamed', 0)} umbenannt, "
            f"{counts.get('moved', 0) + counts.get('moved_renamed', 0)} verschoben, {counts.get('new', 0)} neue Ordner"
        ))
        lay.addLayout(form)
        lay.addWidget(InfoBox(
            "Ablauf: 1. vollständige Kopie erstellen · 2. genehmigte Änderungen <b>nur in der Kopie</b> umsetzen · "
            "3. Ergebnis und Änderungsprotokoll anzeigen. Es wird nichts gelöscht.", "ok"))
        if prep.warnings:
            lay.addWidget(InfoBox("<b>Hinweise:</b>" + html_list(prep.warnings), "warn"))
        if prep.errors:
            lay.addWidget(InfoBox("<b>Die Kopie kann nicht erstellt werden:</b>" + html_list(prep.errors), "error"))
        self.check = QCheckBox("Ich habe die Änderungen geprüft und möchte die Kopie jetzt erstellen.")
        lay.addWidget(self.check)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Kopie erstellen")
        self.ok.setEnabled(False)
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.check.toggled.connect(lambda on: self.ok.setEnabled(on and prep.ok))
        self.check.setEnabled(prep.ok)
        lay.addWidget(bb)

    @staticmethod
    def _sel(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setWordWrap(True)
        return lbl


class EditTargetDialog(QDialog):
    """Eigene Änderung: neuer Pfad für eine Datei bzw. einen Ordner."""

    def __init__(self, source: str, current_target: str, is_dir: bool, parent=None, create_folder: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Neuen Ordner anlegen" if create_folder else "Umbenennen oder verschieben")
        self.resize(620, 200)
        lay = QVBoxLayout(self)
        if create_folder:
            lay.addWidget(QLabel("Pfad des neuen Ordners (relativ zum Hauptordner, „/“ als Trennzeichen):"))
        else:
            lay.addWidget(QLabel(f"{'Ordner' if is_dir else 'Datei'}: <b>{escape(source)}</b>"))
            lay.addWidget(QLabel("Neuer vollständiger Pfad in der neuen Struktur (relativ, „/“ als Trennzeichen):"))
        self.edit = QLineEdit(current_target)
        lay.addWidget(self.edit)
        lay.addWidget(muted("Beispiel: „Dokumente/Rechnungen/Rechnung_2024-03-12.pdf“. Die Änderung wird geprüft, "
                            "bevor sie übernommen wird."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def target(self) -> str:
        return self.edit.text().strip()
