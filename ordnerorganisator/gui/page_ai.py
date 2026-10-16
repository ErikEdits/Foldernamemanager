"""Schritt 3: KI-Auswahl (integriert, lokales Modell, API, Export/Import) und eigene Regeln."""

from __future__ import annotations

import os
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ai.base import AnalysisRequest
from ..ai.exchange import import_plan_files, import_plan_text
from ..ai.gguf import llama_available
from ..ai.openai_compat import OPENROUTER_URL, OpenAICompatibleClient, is_local_url
from ..ai.prompt import build_packages, estimate_tokens, package_id_for
from ..ai.registry import make_provider
from ..content import analyze_contents
from ..filetypes import CATEGORIES
from ..paths import desktop_dir
from ..plan import PlanFormatError
from ..secrets_store import api_key_name
from ..settings import DATE_FORMATS, LANGUAGES, NAMING_STYLES, PROJECT_MODES, UserRules
from .base_page import Page
from .dialogs import ContentSelectionDialog, PackagePreviewDialog, PasteAnswerDialog
from .widgets import InfoBox, html_list, muted
from .workers import Worker

LOCAL_PRESETS = [
    ("llama.cpp-Server", "http://127.0.0.1:8080/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("Ollama (OpenAI-Schnittstelle)", "http://127.0.0.1:11434/v1"),
    ("Jan", "http://127.0.0.1:1337/v1"),
    ("KoboldCpp", "http://127.0.0.1:5001/v1"),
]

PROVIDER_ORDER = ["builtin", "local_model", "api", "exchange"]
PROVIDER_TEXT = {
    "builtin": ("Integrierte KI (Standard)",
                "Kostenlos, offline, ohne Registrierung. Speziell für Ordnerorganisation: erkennt Projektarten, "
                "vereinheitlicht Namen, sortiert nach Dateitypen und fragt bei Unklarheiten nach."),
    "local_model": ("Lokales Sprachmodell",
                    "Ein Sprachmodell auf diesem PC – über eine lokale Laufzeit (z. B. llama.cpp, LM Studio) "
                    "oder direkt als Modelldatei (.gguf). Kostenlos, keine Daten verlassen den PC."),
    "api": ("API-Anbieter (z. B. OpenRouter)",
            "Externe KI über Ihren API-Key. Ordner- und Dateinamen werden an den Anbieter übertragen. "
            "Mögliche Kosten und Limits werden angezeigt."),
    "exchange": ("Cloud-KI per Export/Import (z. B. ChatGPT)",
                 "Ohne API-Key: Paket kopieren oder als Datei speichern, in ChatGPT einfügen und die "
                 "JSON-Antwort wieder importieren."),
}


class PreferredNamesDialog(QDialog):
    def __init__(self, current: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bevorzugte Ordnernamen")
        lay = QVBoxLayout(self)
        lay.addWidget(muted("Leer lassen = Standardname verwenden. Gilt für neu angelegte Sammelordner."))
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for cat in CATEGORIES:
            e = QLineEdit(current.get(cat.key, ""))
            e.setPlaceholderText(f"{cat.folder_de} / {cat.folder_en}")
            self.edits[cat.key] = e
            form.addRow(cat.label_de + ":", e)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self.edits.items() if e.text().strip()}


class AiPage(Page):
    title = "Schritt 3: KI-Auswahl und Regeln"
    subtitle = "Wählen Sie, wer die Vorschläge erstellt, und legen Sie Ihre Vorgaben fest."

    def __init__(self, win):
        super().__init__(win)
        self.worker: Worker | None = None
        self._loading = True
        self._models_cache: dict[str, list] = {}
        self._package_chars: int | None = None
        self._package_parts = 1

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setSpacing(12)
        scroll.setWidget(body)
        self.layout_.addWidget(scroll, 1)

        self._build_provider_group()
        self._build_rules_group()
        self._build_content_group()
        self._build_output_group()
        self.body.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = muted("")
        cancel_row = QHBoxLayout()
        cancel_row.addWidget(self.progress, 1)
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel)
        cancel_row.addWidget(self.cancel_btn)
        self.layout_.addLayout(cancel_row)
        self.layout_.addWidget(self.status)
        self._load_from_settings()
        self._loading = False

    # ------------------------------------------------------------------ UI
    def _build_provider_group(self) -> None:
        box = QGroupBox("KI-Lösung")
        lay = QVBoxLayout(box)
        self.provider_group = QButtonGroup(self)
        self.provider_radios: dict[str, QRadioButton] = {}
        for i, key in enumerate(PROVIDER_ORDER):
            title, desc = PROVIDER_TEXT[key]
            rb = QRadioButton(title)
            f = rb.font()
            f.setBold(True)
            rb.setFont(f)
            self.provider_group.addButton(rb, i)
            self.provider_radios[key] = rb
            lay.addWidget(rb)
            d = muted(desc)
            d.setContentsMargins(24, 0, 0, 4)
            lay.addWidget(d)
        self.panels = [self._builtin_panel(), self._local_panel(), self._api_panel(), self._exchange_panel()]
        for panel in self.panels:
            lay.addWidget(panel)
        lay.addStretch(1)
        self.provider_group.idToggled.connect(self._provider_changed)
        self.body.addWidget(box)

    def _builtin_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(InfoBox(
            "<b>100 % kostenlos und lokal.</b> Keine Internetverbindung, keine Registrierung, keine API-Kosten. "
            "Die integrierte KI kennt nur Namen und Pfade – bei nichtssagenden Namen behält sie den Namen bei "
            "und stellt eine Rückfrage, statt zu raten.", "ok"))
        lay.addStretch(1)
        return w

    def _local_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.local_server_rb = QRadioButton("Lokale Laufzeit (OpenAI-kompatibel)")
        self.local_gguf_rb = QRadioButton("Modelldatei (.gguf) direkt laden")
        self.local_server_rb.setChecked(True)
        row.addWidget(self.local_server_rb)
        row.addWidget(self.local_gguf_rb)
        row.addStretch(1)
        lay.addLayout(row)

        self.local_server_box = QWidget()
        form = QFormLayout(self.local_server_box)
        preset = QComboBox()
        preset.addItem("Voreinstellung wählen …", "")
        for label, url in LOCAL_PRESETS:
            preset.addItem(f"{label} – {url}", url)
        preset.currentIndexChanged.connect(lambda i: preset.itemData(i) and self.local_url.setText(preset.itemData(i)))
        self.local_url = QLineEdit()
        mrow = QHBoxLayout()
        self.local_model = QComboBox()
        self.local_model.setEditable(True)
        self.local_model.lineEdit().setPlaceholderText("Modellname (z. B. von „Modelle laden“)")
        load = QPushButton("Modelle laden")
        load.clicked.connect(lambda: self._load_models(local=True))
        mrow.addWidget(self.local_model, 1)
        mrow.addWidget(load)
        form.addRow("Laufzeit:", preset)
        form.addRow("Adresse:", self.local_url)
        form.addRow("Modell:", mrow)
        lay.addWidget(self.local_server_box)

        self.local_gguf_box = QWidget()
        gform = QFormLayout(self.local_gguf_box)
        grow = QHBoxLayout()
        self.gguf_path = QLineEdit()
        gb = QPushButton("Durchsuchen …")
        gb.clicked.connect(self._browse_gguf)
        grow.addWidget(self.gguf_path, 1)
        grow.addWidget(gb)
        self.gguf_ctx = QSpinBox()
        self.gguf_ctx.setRange(2048, 131072)
        self.gguf_ctx.setSingleStep(2048)
        gform.addRow("Modelldatei:", grow)
        gform.addRow("Kontextgröße:", self.gguf_ctx)
        avail = llama_available()
        gform.addRow("", muted(
            "Laufzeit „llama-cpp-python“ ist installiert." if avail else
            "Hinweis: Dafür wird das Zusatzpaket „llama-cpp-python“ benötigt. Alternativ eine lokale Laufzeit "
            "(z. B. LM Studio) verwenden."))
        lay.addWidget(self.local_gguf_box)
        self.local_server_rb.toggled.connect(self._local_kind_changed)
        lay.addWidget(muted("Läuft vollständig auf diesem PC – kostenlos. Qualität und Geschwindigkeit hängen vom "
                            "Modell und von der Hardware ab."))
        lay.addStretch(1)
        return w

    def _api_panel(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        outer.addLayout(form)
        outer.addStretch(1)
        self.api_kind = QComboBox()
        self.api_kind.addItem("OpenRouter", "openrouter")
        self.api_kind.addItem("Andere OpenAI-kompatible API", "custom")
        self.api_kind.currentIndexChanged.connect(self._api_kind_changed)
        self.api_url = QLineEdit()
        form.addRow("Anbieter:", self.api_kind)
        form.addRow("Adresse:", self.api_url)

        krow = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("API-Key eingeben (wird nie angezeigt, protokolliert oder exportiert)")
        self.key_persist = QCheckBox("dauerhaft sicher speichern")
        self.key_persist.setChecked(self.session.secrets.can_persist)
        self.key_persist.setEnabled(self.session.secrets.can_persist)
        save_key = QPushButton("Übernehmen")
        save_key.clicked.connect(self._save_key)
        del_key = QPushButton("Entfernen")
        del_key.clicked.connect(self._delete_key)
        krow.addWidget(self.api_key, 1)
        krow.addWidget(self.key_persist)
        krow.addWidget(save_key)
        krow.addWidget(del_key)
        form.addRow("API-Key:", krow)
        self.key_status = QLabel("")
        form.addRow("", self.key_status)

        mrow = QHBoxLayout()
        self.api_model = QComboBox()
        self.api_model.setEditable(True)
        self.api_model.lineEdit().setPlaceholderText("Modell-ID, z. B. meta-llama/llama-3.3-70b-instruct:free")
        self.api_model.currentIndexChanged.connect(lambda _i: self._update_cost())
        self.api_model.editTextChanged.connect(lambda _t: self._update_cost())
        self.only_free = QCheckBox("nur kostenlose")
        self.only_free.toggled.connect(lambda _on: self._fill_models(self._models_cache.get("api", []), self.api_model))
        load = QPushButton("Modelle laden")
        load.clicked.connect(lambda: self._load_models(local=False))
        mrow.addWidget(self.api_model, 1)
        mrow.addWidget(self.only_free)
        mrow.addWidget(load)
        form.addRow("Modell:", mrow)
        self.cost_label = InfoBox("", "info")
        form.addRow("Kosten/Limits:", self.cost_label)

        self.external_cb = QCheckBox("Externe KI erlauben – Ordner- und Dateinamen werden an den Anbieter übertragen")
        self.external_cb.toggled.connect(lambda _on: self._settings_changed())
        form.addRow("", self.external_cb)
        prev = QPushButton("Was wird übertragen? (Vorschau)")
        prev.clicked.connect(lambda: self._preview(for_chat=False))
        form.addRow("", prev)
        outer.insertWidget(1, muted("OpenRouter: Schlüssel unter openrouter.ai/keys erstellen. Kostenlose Modelle "
                                    "(Endung „:free“) haben Nutzungslimits; die Verfügbarkeit kann sich ändern."))
        return w

    def _exchange_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>1.</b> Analysepaket erstellen (enthält Struktur, Aufgabe, Regeln und Antwortformat):"))
        r1 = QHBoxLayout()
        b_copy = QPushButton("In Zwischenablage kopieren")
        b_copy.clicked.connect(self._copy_package)
        b_save = QPushButton("Als Datei auf dem Desktop speichern")
        b_save.clicked.connect(lambda: self._preview(for_chat=True, save=True))
        b_prev = QPushButton("Vorschau prüfen")
        b_prev.clicked.connect(lambda: self._preview(for_chat=True))
        for b in (b_copy, b_save, b_prev):
            r1.addWidget(b)
        r1.addStretch(1)
        lay.addLayout(r1)
        lay.addWidget(QLabel("<b>2.</b> In ChatGPT (oder einer anderen KI) einfügen bzw. die Datei hochladen. "
                             "Die KI antwortet mit einem JSON-Plan."))
        lay.addWidget(QLabel("<b>3.</b> Antwort importieren:"))
        r3 = QHBoxLayout()
        b_paste = QPushButton("Antwort einfügen …")
        b_paste.clicked.connect(self._paste_answer)
        b_files = QPushButton("Antwort-Datei(en) öffnen …")
        b_files.clicked.connect(self._open_answers)
        r3.addWidget(b_paste)
        r3.addWidget(b_files)
        r3.addStretch(1)
        lay.addLayout(r3)
        self.exchange_status = InfoBox("Noch kein Plan importiert.", "info")
        lay.addWidget(self.exchange_status)
        lay.addWidget(muted("Die Cloud-KI liefert ausschließlich einen Plan. Alle Dateioperationen führt dieses "
                            "Programm aus – nach Prüfung und nur in der Kopie."))
        lay.addStretch(1)
        return w

    def _build_rules_group(self) -> None:
        box = QGroupBox("Ihre Regeln und Vorlagen")
        outer = QVBoxLayout(box)
        form = QFormLayout()
        outer.addLayout(form)
        outer.addStretch(1)

        trow = QHBoxLayout()
        self.template = QComboBox()
        b_load = QPushButton("Laden")
        b_load.clicked.connect(self._load_template)
        b_save = QPushButton("Als Vorlage speichern …")
        b_save.clicked.connect(self._save_template)
        b_del = QPushButton("Löschen")
        b_del.clicked.connect(self._delete_template)
        trow.addWidget(self.template, 1)
        for b in (b_load, b_save, b_del):
            trow.addWidget(b)
        form.addRow("Vorlage:", trow)

        def combo(options: dict[str, str]) -> QComboBox:
            c = QComboBox()
            for k, v in options.items():
                c.addItem(v, k)
            return c

        self.language = combo(LANGUAGES)
        self.style = combo(NAMING_STYLES)
        self.date_fmt = combo(DATE_FORMATS)
        self.mode = combo(PROJECT_MODES)
        form.addRow("Sprache der Namen:", self.language)
        form.addRow("Namenskonvention:", self.style)
        form.addRow("Datumsformat:", self.date_fmt)
        form.addRow("Projektart:", self.mode)
        self.cb_translate = QCheckBox("bestehende Ordnernamen übersetzen, wenn das einheitlicher ist")
        self.cb_rename_files = QCheckBox("Dateinamen verbessern")
        self.cb_rename_dirs = QCheckBox("Ordnernamen verbessern")
        self.cb_group = QCheckBox("Dateien nach Typ in Unterordner einsortieren")
        self.cb_copy_markers = QCheckBox("Kopie-Kennzeichnungen entfernen („- Kopie“, „(1)“)")
        self.cb_ext = QCheckBox("Dateiendungen kleinschreiben")
        self.cb_umlauts = QCheckBox("Umlaute ersetzen (ä → ae …)")
        for cb in (self.cb_translate, self.cb_rename_files, self.cb_rename_dirs, self.cb_group,
                   self.cb_copy_markers, self.cb_ext, self.cb_umlauts):
            form.addRow("", cb)
        self.min_files = QSpinBox()
        self.min_files.setRange(2, 100)
        form.addRow("Gruppieren ab (Dateien):", self.min_files)
        pref = QPushButton("Bevorzugte Ordnernamen …")
        pref.clicked.connect(self._edit_preferred)
        form.addRow("", pref)
        self.custom = QPlainTextEdit()
        self.custom.setPlaceholderText("Eigene Regeln in Worten (werden an externe KIs übergeben), z. B. "
                                       "„Rechnungen nach Jahr ordnen“")
        self.custom.setMaximumHeight(70)
        form.addRow("Eigene Regeln:", self.custom)
        self.body.addWidget(box)
        self._preferred: dict[str, str] = {}

    def _build_content_group(self) -> None:
        box = QGroupBox("Optionale Inhaltsanalyse")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.content_cb = QCheckBox("Ausgewählte Dateiinhalte zusätzlich analysieren")
        self.content_btn = QPushButton("Dateien auswählen …")
        self.content_btn.clicked.connect(self._select_content)
        row.addWidget(self.content_cb)
        row.addWidget(self.content_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.content_info = muted("")
        lay.addWidget(self.content_info)
        self.content_cb.toggled.connect(lambda _on: self._update_content_info())
        lay.addStretch(1)
        self.body.addWidget(box)

    def _build_output_group(self) -> None:
        box = QGroupBox("Speicherort der Kopie")
        row = QHBoxLayout(box)
        self.output_label = QLabel()
        self.output_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        change = QPushButton("Ändern …")
        change.clicked.connect(self._choose_output)
        reset = QPushButton("Desktop")
        reset.clicked.connect(lambda: self._set_output(""))
        row.addWidget(self.output_label, 1)
        row.addWidget(change)
        row.addWidget(reset)
        self.body.addWidget(box)

    # ------------------------------------------------------- Einstellungen
    def _load_from_settings(self) -> None:
        s = self.session.settings
        self.provider_radios.get(s.provider, self.provider_radios["builtin"]).setChecked(True)
        self._show_panel(PROVIDER_ORDER.index(s.provider) if s.provider in PROVIDER_ORDER else 0)
        lm = s.local_model
        self.local_url.setText(lm.base_url)
        self.local_model.setCurrentText(lm.model)
        self.gguf_path.setText(lm.gguf_path)
        self.gguf_ctx.setValue(lm.context_size)
        (self.local_gguf_rb if lm.kind == "gguf" else self.local_server_rb).setChecked(True)
        self._local_kind_changed()
        self.api_kind.setCurrentIndex(0 if s.api.provider_kind == "openrouter" else 1)
        self.api_url.setText(s.api.base_url)
        self.api_model.setCurrentText(s.api.model)
        self.only_free.setChecked(s.api.only_free_models)
        self.external_cb.setChecked(s.external_ai_enabled)
        self._api_kind_changed()
        self._apply_rules(s.rules)
        self.content_cb.setChecked(s.content_analysis_enabled)
        self._refresh_templates()
        self._set_output(s.output_dir)

    def _apply_rules(self, r: UserRules) -> None:
        def set_combo(c: QComboBox, value: str) -> None:
            i = c.findData(value)
            c.setCurrentIndex(max(0, i))

        set_combo(self.language, r.language)
        set_combo(self.style, r.naming_style)
        set_combo(self.date_fmt, r.date_format)
        set_combo(self.mode, r.project_mode)
        self.cb_translate.setChecked(r.translate_existing)
        self.cb_rename_files.setChecked(r.rename_files)
        self.cb_rename_dirs.setChecked(r.rename_folders)
        self.cb_group.setChecked(r.group_by_type)
        self.cb_copy_markers.setChecked(r.remove_copy_markers)
        self.cb_ext.setChecked(r.lowercase_extensions)
        self.cb_umlauts.setChecked(r.transliterate_umlauts)
        self.min_files.setValue(r.min_files_for_grouping)
        self.custom.setPlainText(r.custom_instructions)
        self._preferred = dict(r.preferred_folder_names)

    def collect_rules(self) -> UserRules:
        return UserRules(
            language=self.language.currentData(),
            translate_existing=self.cb_translate.isChecked(),
            naming_style=self.style.currentData(),
            date_format=self.date_fmt.currentData(),
            lowercase_extensions=self.cb_ext.isChecked(),
            transliterate_umlauts=self.cb_umlauts.isChecked(),
            remove_copy_markers=self.cb_copy_markers.isChecked(),
            rename_files=self.cb_rename_files.isChecked(),
            rename_folders=self.cb_rename_dirs.isChecked(),
            group_by_type=self.cb_group.isChecked(),
            min_files_for_grouping=self.min_files.value(),
            project_mode=self.mode.currentData(),
            preferred_folder_names=dict(self._preferred),
            custom_instructions=self.custom.toPlainText().strip(),
        )

    def collect_settings(self) -> None:
        s = self.session.settings
        s.provider = PROVIDER_ORDER[max(0, self.provider_group.checkedId())]
        s.rules = self.collect_rules()
        s.local_model.kind = "gguf" if self.local_gguf_rb.isChecked() else "server"
        s.local_model.base_url = self.local_url.text().strip()
        s.local_model.model = self._combo_value(self.local_model)
        s.local_model.gguf_path = self.gguf_path.text().strip()
        s.local_model.context_size = self.gguf_ctx.value()
        s.api.provider_kind = self.api_kind.currentData()
        s.api.base_url = self.api_url.text().strip() or OPENROUTER_URL
        s.api.model = self._combo_value(self.api_model)
        s.api.only_free_models = self.only_free.isChecked()
        s.external_ai_enabled = self.external_cb.isChecked()
        s.content_analysis_enabled = self.content_cb.isChecked()
        s.save()

    @staticmethod
    def _combo_value(c: QComboBox) -> str:
        idx = c.currentIndex()
        if idx >= 0 and c.itemText(idx) == c.currentText() and c.itemData(idx):
            return str(c.itemData(idx))
        return c.currentText().strip()

    def _settings_changed(self) -> None:
        if self._loading:
            return
        self.collect_settings()
        self._update_content_info()
        self.win.update_status()
        self.win.update_nav()

    # -------------------------------------------------------------- Events
    def enter(self) -> None:
        self._package_chars = None
        self._update_key_status()
        self._update_content_info()
        self._update_cost()
        self._update_exchange_status()

    def _show_panel(self, idx: int) -> None:
        """Zeigt nur das Konfigurationsfeld der gewählten KI-Lösung."""
        for i, panel in enumerate(self.panels):
            panel.setVisible(i == idx)

    def _provider_changed(self, idx: int, checked: bool) -> None:
        if not checked:
            return
        self._show_panel(idx)
        self._settings_changed()
        self._update_content_info()
        if not self._loading:
            self._update_cost()

    def _local_kind_changed(self) -> None:
        server = self.local_server_rb.isChecked()
        self.local_server_box.setVisible(server)
        self.local_gguf_box.setVisible(not server)

    def _api_kind_changed(self) -> None:
        openrouter = self.api_kind.currentData() == "openrouter"
        if openrouter:
            self.api_url.setText(OPENROUTER_URL)
        self.api_url.setReadOnly(openrouter)
        self._update_key_status()

    def _browse_gguf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Modelldatei wählen", "", "GGUF-Modelle (*.gguf)")
        if path:
            self.gguf_path.setText(os.path.normpath(path))

    def _set_output(self, path: str) -> None:
        self.session.settings.output_dir = path
        shown = path or f"Desktop ({desktop_dir()})"
        self.output_label.setText(shown)
        if not self._loading:
            self.session.settings.save()
            self.win.update_status()

    def _choose_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Ordner für die Kopie wählen", str(desktop_dir()))
        if chosen:
            self._set_output(os.path.normpath(chosen))

    # -- API-Key ---------------------------------------------------------
    def _key_name(self) -> str:
        return api_key_name(self.api_kind.currentData())

    def _update_key_status(self) -> None:
        if self.session.secrets.has(self._key_name()):
            self.key_status.setText(f"✔ Schlüssel hinterlegt ({self.session.secrets.backend_name}).")
        else:
            self.key_status.setText(f"Kein Schlüssel hinterlegt. Speicherort: {self.session.secrets.backend_name}.")

    def _save_key(self) -> None:
        key = self.api_key.text().strip()
        if not key:
            return
        try:
            self.session.secrets.set(self._key_name(), key, persist=self.key_persist.isChecked())
        except Exception as exc:  # pragma: no cover - systemabhängig
            QMessageBox.warning(self, "Speichern fehlgeschlagen", f"Der Schlüssel konnte nicht gespeichert werden: {exc}")
        self.api_key.clear()
        self._update_key_status()

    def _delete_key(self) -> None:
        self.session.secrets.delete(self._key_name())
        self._update_key_status()

    # -- Modelle & Kosten ----------------------------------------------------
    def _load_models(self, local: bool) -> None:
        self.collect_settings()
        if local:
            url, key = self.local_url.text().strip(), None
        else:
            url = self.api_url.text().strip()
            key = self.session.secrets.get(self._key_name())
            if not is_local_url(url) and not self.external_cb.isChecked():
                QMessageBox.information(self, "Externe KI deaktiviert",
                                        "Bitte zuerst „Externe KI erlauben“ aktivieren, um Modelle abzurufen.")
                return
        target = self.local_model if local else self.api_model

        def task(_p, _c):
            return OpenAICompatibleClient(url, key, timeout=30).list_models()

        def done(models):
            self._models_cache["local" if local else "api"] = models
            self._fill_models(models, target)
            self.status.setText(f"{len(models)} Modelle geladen.")
            self._update_cost()

        self._run(task, done, "Modelle werden geladen …")

    def _fill_models(self, models: list, combo: QComboBox) -> None:
        current = self._combo_value(combo)
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            if combo is self.api_model and self.only_free.isChecked() and not m.is_free:
                continue
            combo.addItem(f"{m.id} – {m.price_label()}", m.id)
        combo.blockSignals(False)
        i = combo.findData(current)
        if i >= 0:
            combo.setCurrentIndex(i)
        elif current:
            combo.setEditText(current)

    def _model_info(self):
        mid = self._combo_value(self.api_model)
        for m in self._models_cache.get("api", []):
            if m.id == mid:
                return m
        return None

    def _update_cost(self) -> None:
        # Nur berechnen, wenn der API-Anbieter gewählt ist (bei großen Ordnern spürbar).
        if self.session.snapshot is None or PROVIDER_ORDER[max(0, self.provider_group.checkedId())] != "api":
            return
        if self._package_chars is None:
            pk, privacy = build_packages(self._request(), for_chat=False)
            self._package_chars, self._package_parts = privacy.chars, privacy.parts
        tokens = estimate_tokens(self._package_chars)
        out_tokens = min(max(500, tokens // 2), 16000 * self._package_parts)
        m = self._model_info()
        text = f"Umfang: ca. {tokens:,} Eingabe-Tokens in {self._package_parts} Anfrage(n)".replace(",", ".")
        if m is None:
            text += ". Kosten: unbekannt – „Modelle laden“, um Preise anzuzeigen."
        elif m.is_free:
            text += ". <b>Kostenlos</b> – der Anbieter begrenzt aber die Nutzung (Anfragen pro Minute/Tag)."
        else:
            cost = m.estimate_cost(tokens, out_tokens)
            if cost is None:
                text += ". Preis unbekannt."
            else:
                shown = f"{cost:.4f}".replace(".", ",")
                text += f". Geschätzte Kosten: <b>ca. {shown} $</b> ({escape(m.price_label())}) – Schätzung."
        if m is not None and m.context_length and tokens > m.context_length * 0.8:
            text += f"<br>⚠ Das Paket ist evtl. zu groß für das Kontextfenster dieses Modells ({m.context_length} Tokens)."
        self.cost_label.set_text(text, "info")

    # -- Inhaltsanalyse -----------------------------------------------------
    def _target_desc(self) -> str:
        p = PROVIDER_ORDER[max(0, self.provider_group.checkedId())]
        if p == "api" and not is_local_url(self.api_url.text()):
            return "Auszüge werden an den API-Anbieter übertragen"
        if p == "exchange":
            return "Auszüge werden in das Analysepaket für die Cloud-KI übernommen"
        return "nur lokal auf diesem PC"

    def _update_content_info(self) -> None:
        on = self.content_cb.isChecked()
        self.content_btn.setEnabled(on and self.session.snapshot is not None)
        n = len(self.session.content_selection)
        if not on:
            self.content_info.setText("Aus – es werden ausschließlich Namen, Pfade und Dateitypen verwendet.")
        else:
            self.content_info.setText(f"{n} Datei(en) ausgewählt · Verwendung: {self._target_desc()}.")

    def _select_content(self) -> None:
        if self.session.snapshot is None:
            return
        dlg = ContentSelectionDialog(self.session.snapshot, self.session.content_selection, self._target_desc(), self)
        if dlg.exec():
            self.session.content_selection = dlg.selected()
            self.session.contents = {}
            self._package_chars = None
            self._update_content_info()

    def _ensure_contents(self) -> dict:
        if not self.content_cb.isChecked() or not self.session.content_selection or self.session.snapshot is None:
            return {}
        if set(self.session.contents) != set(self.session.content_selection):
            self.session.contents = analyze_contents(self.session.snapshot, self.session.content_selection)
        return self.session.contents

    # -- Vorlagen ----------------------------------------------------------
    def _refresh_templates(self) -> None:
        self.template.clear()
        self.template.addItems(sorted(self.session.settings.templates))

    def _load_template(self) -> None:
        name = self.template.currentText()
        if name in self.session.settings.templates:
            self._apply_rules(self.session.settings.load_template(name))

    def _save_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Vorlage speichern", "Name der Vorlage:")
        if ok and name.strip():
            self.session.settings.save_template(name.strip(), self.collect_rules())
            self.session.settings.save()
            self._refresh_templates()
            self.template.setCurrentText(name.strip())

    def _delete_template(self) -> None:
        name = self.template.currentText()
        if name:
            self.session.settings.delete_template(name)
            self.session.settings.save()
            self._refresh_templates()

    def _edit_preferred(self) -> None:
        dlg = PreferredNamesDialog(self._preferred, self)
        if dlg.exec():
            self._preferred = dlg.values()

    # -- Export/Import -----------------------------------------------------
    def _request(self) -> AnalysisRequest:
        s = self.session.settings
        return AnalysisRequest(
            snapshot=self.session.snapshot,
            rules=self.collect_rules(),
            contents=self._ensure_contents(),
            include_hidden=s.include_hidden_in_package,
            include_absolute_root=s.include_absolute_root,
            max_package_chars=s.max_package_chars,
        )

    def _preview(self, for_chat: bool, save: bool = False) -> None:
        if self.session.snapshot is None:
            return
        self.collect_settings()
        packages, privacy = build_packages(self._request(), for_chat=for_chat)
        target = "die Cloud-KI" if for_chat else "der API-Anbieter"
        dlg = PackagePreviewDialog(packages, privacy, self.session.snapshot.root_name, self,
                                   allow_export=for_chat, target_desc=target)
        if save:
            dlg.save_files()
        dlg.exec()

    def _copy_package(self) -> None:
        if self.session.snapshot is None:
            return
        self.collect_settings()
        packages, privacy = build_packages(self._request(), for_chat=True)
        if len(packages) > 1:
            self._preview(for_chat=True)
            return
        dlg = PackagePreviewDialog(packages, privacy, self.session.snapshot.root_name, self)
        dlg.copy_current()

    def _paste_answer(self) -> None:
        dlg = PasteAnswerDialog(self)
        if not dlg.exec():
            return
        try:
            plan = import_plan_text(dlg.text(), origin="import:zwischenablage",
                                    expected_package_id=package_id_for(self.session.snapshot))
        except (PlanFormatError, ValueError) as exc:
            QMessageBox.warning(self, "Import nicht möglich", str(exc))
            return
        self._accept_imported(plan, "Cloud-KI (eingefügte Antwort)")

    def _open_answers(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Antwort-Datei(en) der KI öffnen", str(desktop_dir()),
                                                "KI-Antworten (*.json *.txt *.md);;Alle Dateien (*)")
        if not paths:
            return
        try:
            plan = import_plan_files(paths, expected_package_id=package_id_for(self.session.snapshot))
        except (PlanFormatError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import nicht möglich", str(exc))
            return
        self._accept_imported(plan, f"Cloud-KI ({len(paths)} Datei(en))")

    def _accept_imported(self, plan, label: str) -> None:
        if not plan.operations:
            QMessageBox.information(self, "Leerer Plan", "Der importierte Plan enthält keine Anweisungen.")
        if self.session.plan is not None and self.session.plan.provider.startswith("import") and \
                QMessageBox.question(self, "Teilantwort ergänzen?",
                                     "Es wurde bereits ein Plan importiert. Soll der neue Plan ergänzt werden "
                                     "(z. B. weiterer Teil)? „Nein“ ersetzt den bisherigen Plan.") == QMessageBox.Yes:
            self.session.plan.merge(plan)
            plan = self.session.plan
        self.win.set_plan(plan, label)
        self._update_exchange_status()
        self.win.go_next()

    def _update_exchange_status(self) -> None:
        plan = self.session.plan
        if plan is not None and plan.provider.startswith("import"):
            self.exchange_status.set_text(f"✔ Plan importiert: {len(plan.operations)} Anweisungen.", "ok")
        else:
            self.exchange_status.set_text("Noch kein Plan importiert.", "info")

    # -- Analyse starten ----------------------------------------------------
    def next_label(self) -> str:
        if PROVIDER_ORDER[max(0, self.provider_group.checkedId())] == "exchange":
            return "Weiter zum Ergebnis"
        return "Analyse starten"

    def next_enabled(self) -> bool:
        if self.busy():
            return False
        if PROVIDER_ORDER[max(0, self.provider_group.checkedId())] == "exchange":
            return self.session.plan is not None and self.session.plan.provider.startswith("import")
        return True

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _run(self, task, done, message: str) -> None:
        self.progress.show()
        self.cancel_btn.show()
        self.status.setText(message)
        self.worker = Worker(task, self)
        self.worker.progress.connect(lambda m: self.status.setText(str(m)))
        self.worker.succeeded.connect(lambda r: (self._idle(), done(r)))
        self.worker.failed.connect(lambda msg: (self._idle(), QMessageBox.warning(self, "Fehler", msg)))
        self.worker.cancelled.connect(lambda: (self._idle(), self.status.setText("Abgebrochen.")))
        self.worker.start()
        self.win.update_nav()

    def _idle(self) -> None:
        self.progress.hide()
        self.cancel_btn.hide()
        self.status.setText("")
        self.win.update_nav()

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def on_next(self) -> None:
        self.collect_settings()
        s = self.session.settings
        if s.provider == "exchange":
            self.win.go_next()
            return
        try:
            provider = make_provider(s, self.session.secrets)
        except Exception as exc:
            QMessageBox.warning(self, "KI nicht verfügbar", str(exc))
            return
        request = self._request()
        if provider.is_external:
            if not s.external_ai_enabled:
                QMessageBox.information(self, "Externe KI deaktiviert",
                                        "Bitte „Externe KI erlauben“ aktivieren oder eine lokale KI wählen.")
                return
            _pk, privacy = build_packages(request, for_chat=False)
            msg = QMessageBox(self)
            msg.setWindowTitle("Daten an externe KI senden?")
            msg.setTextFormat(Qt.RichText)
            msg.setText(f"<b>{escape(provider.describe())}</b> erhält:" + html_list(privacy.lines()) +
                        f"<br>{self.cost_label.label.text()}")
            send = msg.addButton("Senden und analysieren", QMessageBox.AcceptRole)
            msg.addButton("Abbrechen", QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() is not send:
                return

        def task(progress, cancel):
            plan = provider.analyze(request, progress=progress, cancel=cancel)
            return plan, provider.describe(), provider.last_usage

        def done(result):
            plan, label, usage = result
            self.session.usage = usage
            self.win.set_plan(plan, label)
            self.win.go_next()

        self._run(task, done, f"{provider.describe()} analysiert …")
