"""Hauptfenster mit Schrittleiste und Navigation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..ai.registry import describe_active
from ..executor import RunState
from ..paths import desktop_dir
from ..plan import Plan
from ..planner import ERROR, resolve_plan
from ..secrets_store import SecretStore
from ..settings import AppSettings
from .page_ai import AiPage
from .pages_review import ResultPage, ReviewPage
from .pages_run import ExecutePage, FinishPage
from .pages_start import StartPage, StructurePage
from .session import Session

STEPS = [
    "1  Ordner auswählen",
    "2  Struktur ansehen",
    "3  KI-Auswahl",
    "4  Analyseergebnis",
    "5  Vergleich & Freigabe",
    "6  Ausführung",
    "7  Abschluss",
]
EXECUTE_INDEX = 5


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None, secrets: SecretStore | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1240, 820)
        self.session = Session(settings or AppSettings.load(), secrets or SecretStore())
        self.invalid_messages: dict[str, list[str]] = {}
        self.provider_status = QLabel()
        self.target_status = QLabel()

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QWidget()
        side.setFixedWidth(220)
        side.setObjectName("sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 8, 12)
        title = QLabel(APP_NAME)
        f = QFont(title.font())
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        title.setWordWrap(True)
        sl.addWidget(title)
        self.steps = QListWidget()
        self.steps.setFocusPolicy(Qt.NoFocus)
        self.steps.setFrameShape(QListWidget.NoFrame)
        self.steps.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { padding: 7px 4px; }")
        for s in STEPS:
            it = QListWidgetItem(s)
            it.setFlags(Qt.ItemIsEnabled)
            self.steps.addItem(it)
        sl.addWidget(self.steps, 1)
        safe = QLabel("🔒 Das Original wird nie verändert.")
        safe.setWordWrap(True)
        sl.addWidget(safe)
        outer.addWidget(side)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 12, 12)
        self.stack = QStackedWidget()
        self.pages = [
            StartPage(self), StructurePage(self), AiPage(self), ResultPage(self),
            ReviewPage(self), ExecutePage(self), FinishPage(self),
        ]
        for p in self.pages:
            self.stack.addWidget(p)
        rl.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        self.back_btn = QPushButton("Zurück")
        self.back_btn.clicked.connect(self.go_back)
        self.next_btn = QPushButton("Weiter")
        self.next_btn.setDefault(True)
        self.next_btn.setMinimumWidth(200)
        self.next_btn.clicked.connect(lambda: self.current_page().on_next())
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)
        rl.addLayout(nav)
        outer.addWidget(right, 1)
        self.setCentralWidget(central)

        self.statusBar().addWidget(self.provider_status, 1)
        self.statusBar().addPermanentWidget(self.target_status)
        self._build_menu()
        self.go_to(0)

    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&Datei")
        a = QAction("Neuer Vorgang", self)
        a.triggered.connect(self.new_session)
        m.addAction(a)
        m.addSeparator()
        q = QAction("Beenden", self)
        q.triggered.connect(self.close)
        m.addAction(q)
        h = self.menuBar().addMenu("&Hilfe")
        g = QAction("Kurzanleitung", self)
        g.triggered.connect(self.show_help)
        h.addAction(g)
        about = QAction("Über", self)
        about.triggered.connect(self.show_about)
        h.addAction(about)

    def show_help(self) -> None:
        QMessageBox.information(self, "Kurzanleitung", (
            "1. Ordner wählen – das Original wird nur gelesen.\n"
            "2. Struktur ansehen und durchsuchen.\n"
            "3. KI wählen: integrierte KI (kostenlos, offline), lokales Modell, API-Anbieter wie OpenRouter "
            "oder Cloud-KI per Export/Import (z. B. ChatGPT).\n"
            "4. Vorschläge, Begründungen und Rückfragen lesen.\n"
            "5. Vorher/Nachher vergleichen, einzelne Vorschläge annehmen, ablehnen oder ändern.\n"
            "6. Nach Bestätigung entsteht eine Kopie auf dem Desktop; nur dort werden die Änderungen umgesetzt.\n"
            "7. Speicherort und Änderungsprotokoll ansehen – bei Bedarf rückgängig machen.\n\n"
            "Es werden niemals Dateien gelöscht."
        ))

    def show_about(self) -> None:
        QMessageBox.about(self, "Über", (
            f"<b>{APP_NAME} {__version__}</b><br>Sichere, KI-gestützte Organisation von Ordnern.<br><br>"
            "Das Original bleibt unverändert – alle Änderungen erfolgen nach Ihrer Freigabe in einer Kopie."
        ))

    def current_page(self):
        return self.stack.currentWidget()

    def go_to(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i in range(self.steps.count()):
            it = self.steps.item(i)
            f = it.font()
            f.setBold(i == index)
            it.setFont(f)
            it.setForeground(self.palette().text() if i <= index else self.palette().placeholderText())
        self.steps.setCurrentRow(index)
        self.current_page().enter()
        self.update_nav()
        self.update_status()

    def go_next(self) -> None:
        i = self.stack.currentIndex()
        if i < len(self.pages) - 1:
            self.go_to(i + 1)

    def go_back(self) -> None:
        i = self.stack.currentIndex()
        if i > 0 and self.current_page().back_enabled() and not self.current_page().busy():
            self.go_to(i - 1)

    def update_nav(self) -> None:
        page = self.current_page()
        idx = self.stack.currentIndex()
        self.back_btn.setEnabled(idx > 0 and page.back_enabled() and not page.busy())
        self.next_btn.setText(page.next_label())
        self.next_btn.setEnabled(page.next_enabled() and not page.busy())

    def update_status(self) -> None:
        s = self.session.settings
        text = describe_active(s)
        if self.session.snapshot is not None:
            text += f"   ·   Ordner: {self.session.snapshot.root_path}"
        self.provider_status.setText(text)
        self.target_status.setText(f"Kopien werden erstellt in: {s.output_dir or desktop_dir()}")

    # ------------------------------------------------------------------
    def set_plan(self, plan: Plan, label: str) -> None:
        """Übernimmt einen Plan, prüft ihn und weist ungültige Anweisungen zurück."""
        snap = self.session.snapshot
        self.session.plan = plan
        self.session.provider_label = label
        invalid: dict[str, list[str]] = {}
        for op in plan.operations:
            if op.parse_errors:
                op.accepted = False
                invalid[op.id] = list(op.parse_errors)
        res = None
        for _ in range(len(plan.operations) + 2):
            res = resolve_plan(snap, plan)
            bad = [i for i in res.issues if i.level == ERROR and i.op_id]
            if not bad:
                break
            for issue in bad:
                invalid.setdefault(issue.op_id, []).append(issue.message)
            for op in plan.operations:
                if op.id in invalid:
                    op.accepted = False
        self.invalid_messages = invalid
        self.session.resolution = res
        self.session.rejected_invalid = [op for op in plan.operations if op.id in invalid]

    def resume_run(self, run_id: str) -> None:
        try:
            state = RunState.load(run_id)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Vorgang nicht lesbar", str(exc))
            return
        self.session.reset()
        self.session.run = state
        self.go_to(EXECUTE_INDEX)

    def new_session(self) -> None:
        if any(p.busy() for p in self.pages):
            QMessageBox.information(self, "Bitte warten", "Es läuft noch ein Vorgang.")
            return
        self.session.reset()
        self.invalid_messages = {}
        self.go_to(0)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-API
        busy = [p for p in self.pages if p.busy()]
        if busy:
            if QMessageBox.question(
                self, "Beenden?",
                "Es läuft noch ein Vorgang. Beim Beenden wird er sicher angehalten und kann später fortgesetzt "
                "werden. Jetzt beenden?",
            ) != QMessageBox.Yes:
                event.ignore()
                return
            for p in busy:
                worker = getattr(p, "worker", None)
                if worker is not None:
                    worker.cancel()
                    worker.wait(15000)
        try:
            self.session.settings.save()
        except OSError:
            pass
        event.accept()
