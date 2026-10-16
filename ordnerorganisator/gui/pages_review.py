"""Schritt 4 (Analyseergebnis) und Schritt 5 (Vergleich und Freigabe)."""

from __future__ import annotations

import os
from html import escape

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..executor import create_run, prepare_run
from ..paths import desktop_dir
from ..plan import CREATE_FOLDER, MOVE, RENAME, Operation
from ..planner import ERROR, INFO, WARNING, resolve_plan
from ..windows_names import PathError, join_rel, normalize_rel_path, parent_of
from .base_page import Page
from .dialogs import ConfirmExecutionDialog, EditTargetDialog
from .widgets import InfoBox, SearchableTree, build_final_model, build_snapshot_model, html_list, legend

COLOR_ERROR = QColor("#c0392b")
COLOR_WARN = QColor("#c77d14")
COLOR_OK = QColor("#2e9d4f")


def confidence_text(op: Operation) -> str:
    return "–" if op.confidence is None else f"{round(op.confidence * 100)} %"


class ResultPage(Page):
    title = "Schritt 4: Analyseergebnis"
    subtitle = "Vorschläge, Begründungen, Hinweise und Rückfragen der KI."

    def __init__(self, win):
        super().__init__(win)
        self.header = InfoBox("", "info")
        self.layout_.addWidget(self.header)
        self.notes = InfoBox("", "warn")
        self.invalid = InfoBox("", "error")
        self.warnings = InfoBox("", "warn")
        self.questions = InfoBox("", "info")
        for w in (self.notes, self.invalid, self.warnings, self.questions):
            self.layout_.addWidget(w)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Aktion", "Bisher", "Neu", "Sicherheit", "Begründung"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setWordWrap(False)
        self.layout_.addWidget(self.table, 1)

    def enter(self) -> None:
        plan = self.session.plan
        if plan is None:
            return
        usage = self.session.usage
        extra = ""
        if usage and usage.requests:
            extra = f"<br>Anfragen: {usage.requests} · Tokens: {usage.prompt_tokens + usage.completion_tokens}"
            if usage.cost_usd is not None:
                extra += f" · Kosten laut Anbieter: {usage.cost_usd:.4f} $".replace(".", ",")
        self.header.set_text(
            f"<b>Erstellt von:</b> {escape(self.session.provider_label)}{extra}<br><br>"
            f"{escape(plan.summary or 'Keine Zusammenfassung.')}", "info")
        self.notes.setVisible(bool(plan.import_notes))
        self.notes.set_text("<b>Hinweise zum Import:</b>" + html_list(plan.import_notes))
        inv = self.win.invalid_messages
        self.invalid.setVisible(bool(inv))
        self.invalid.set_text(
            f"<b>{len(inv)} Anweisung(en) wurden als ungültig zurückgewiesen</b> (sie werden nicht ausgeführt):"
            + html_list([f"{oid}: {'; '.join(msgs)}" for oid, msgs in inv.items()], 12))
        self.warnings.setVisible(bool(plan.warnings))
        self.warnings.set_text("<b>Hinweise:</b>" + html_list(plan.warnings))
        self.questions.setVisible(bool(plan.questions))
        self.questions.set_text(
            "<b>Rückfragen der KI:</b>" + html_list(plan.questions)
            + "<i>Sie können Namen im nächsten Schritt selbst anpassen.</i>")
        self.table.setRowCount(len(plan.operations))
        for r, op in enumerate(plan.operations):
            label = op.kind_label()
            if not op.accepted and op.id not in inv:
                label += " (nicht vorausgewählt)"
            items = [label, op.source or "", op.target, confidence_text(op), op.reason]
            for c, text in enumerate(items):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                if op.id in inv:
                    it.setForeground(QBrush(COLOR_ERROR))
                elif not op.accepted:
                    it.setForeground(QBrush(QColor(128, 128, 128)))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        for c in (1, 2):
            if self.table.columnWidth(c) > 320:
                self.table.setColumnWidth(c, 320)

    def next_label(self) -> str:
        return "Weiter zu Vergleich und Freigabe"


class ReviewPage(Page):
    title = "Schritt 5: Vergleich und Freigabe"
    subtitle = ("Links das Original, rechts die Vorschau der Kopie. Haken setzen oder entfernen, Zielnamen per "
                "Doppelklick ändern oder eigene Änderungen über das Kontextmenü (rechte Maustaste) hinzufügen.")

    def __init__(self, win):
        super().__init__(win)
        self._loading = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.revalidate)

        vsplit = QSplitter(Qt.Vertical)
        trees = QSplitter(Qt.Horizontal)
        self.before = SearchableTree("Vorher – Original (bleibt unverändert)")
        self.after = SearchableTree("Nachher – Vorschau der Kopie")
        self.before.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.before.view.customContextMenuRequested.connect(self._context_menu)
        after_box = QWidget()
        al = QVBoxLayout(after_box)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.after, 1)
        al.addWidget(legend())
        trees.addWidget(self.before)
        trees.addWidget(after_box)
        vsplit.addWidget(trees)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for text, fn in (
            ("Alle annehmen", lambda: self._set_all(True)),
            ("Alle ablehnen", lambda: self._set_all(False)),
            ("Nur sichere (≥ 70 %)", self._only_safe),
            ("Eigene Änderung …", self._add_for_selected),
            ("Neuer Ordner …", self._add_folder),
            ("Plan speichern …", self._save_plan),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Übernehmen", "Aktion", "Bisher", "Neu (bearbeitbar)", "Sicherheit",
                                              "Begründung", "Prüfung"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        bl.addWidget(self.table, 1)
        self.summary = InfoBox("", "info")
        bl.addWidget(self.summary)
        self.issues = QListWidget()
        self.issues.setMaximumHeight(110)
        self.issues.itemClicked.connect(self._issue_clicked)
        bl.addWidget(self.issues)
        vsplit.addWidget(bottom)
        vsplit.setSizes([380, 360])
        self.layout_.addWidget(vsplit, 1)

    # ------------------------------------------------------------------
    def dest_estimate(self) -> str:
        snap = self.session.snapshot
        parent = self.session.settings.output_dir or str(desktop_dir())
        return os.path.join(parent, f"{snap.root_name}_Organisiert") if snap else parent

    def enter(self) -> None:
        if self.session.snapshot is None or self.session.plan is None:
            return
        self.before.set_model(build_snapshot_model(self.session.snapshot, self.session.settings.show_hidden_in_tree))
        self._fill_table()
        self.revalidate()

    def _fill_table(self) -> None:
        plan = self.session.plan
        self._loading = True
        self.table.setRowCount(len(plan.operations))
        for r, op in enumerate(plan.operations):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if op.accepted else Qt.Unchecked)
            check.setData(Qt.UserRole, op.id)
            if not op.executable:
                check.setFlags(Qt.ItemIsSelectable)
                check.setToolTip("; ".join(op.parse_errors))
            cells = [check, QTableWidgetItem(op.kind_label()), QTableWidgetItem(op.source or ""),
                     QTableWidgetItem(op.target), QTableWidgetItem(confidence_text(op)),
                     QTableWidgetItem(op.reason), QTableWidgetItem("")]
            for c, it in enumerate(cells):
                if c != 3 and c != 0:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c in (2, 3, 5):
                    it.setToolTip(it.text())
                self.table.setItem(r, c, it)
            if not op.executable:
                self.table.item(r, 3).setFlags(Qt.ItemIsSelectable)
        self._loading = False
        self.table.resizeColumnsToContents()
        for c in (2, 3):
            if self.table.columnWidth(c) > 300:
                self.table.setColumnWidth(c, 300)
        self.table.setColumnWidth(6, 260)

    def _op_for_row(self, row: int) -> Operation | None:
        it = self.table.item(row, 0)
        return self.session.plan.by_id(it.data(Qt.UserRole)) if it else None

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        op = self._op_for_row(item.row())
        if op is None:
            return
        if item.column() == 0:
            op.accepted = item.checkState() == Qt.Checked and op.executable
        elif item.column() == 3:
            try:
                new_target = normalize_rel_path(item.text())
                if not new_target:
                    raise PathError("Der Zielpfad darf nicht leer sein.")
            except PathError as exc:
                QMessageBox.warning(self, "Ungültiger Pfad", str(exc))
                self._loading = True
                item.setText(op.target)
                self._loading = False
                return
            if new_target != op.target:
                op.target = new_target
                op.modified = True
                op.accepted = True
                self._loading = True
                item.setText(new_target)
                self.table.item(item.row(), 0).setCheckState(Qt.Checked)
                self.table.item(item.row(), 1).setText(op.kind_label())
                self._loading = False
        self.timer.start()

    def revalidate(self) -> None:
        snap, plan = self.session.snapshot, self.session.plan
        if snap is None or plan is None:
            return
        res = resolve_plan(snap, plan, dest_root=self.dest_estimate())
        self.session.resolution = res
        self.after.set_model(build_final_model(res), 1)
        by_op: dict[str, list] = {}
        for issue in res.issues:
            by_op.setdefault(issue.op_id, []).append(issue)
        self._loading = True
        for r in range(self.table.rowCount()):
            op = self._op_for_row(r)
            cell = self.table.item(r, 6)
            if op is None or cell is None:
                continue
            issues = by_op.get(op.id, [])
            errs = [i for i in issues if i.level == ERROR]
            warns = [i for i in issues if i.level in (WARNING, INFO)]
            if not op.accepted:
                msg = "nicht übernommen"
                if op.id in self.win.invalid_messages:
                    msg = "✗ " + "; ".join(self.win.invalid_messages[op.id])
                cell.setText(msg)
                cell.setForeground(QBrush(COLOR_ERROR if op.id in self.win.invalid_messages else QColor(128, 128, 128)))
            elif errs:
                cell.setText("✗ " + "; ".join(i.message for i in errs))
                cell.setForeground(QBrush(COLOR_ERROR))
            elif warns:
                cell.setText("! " + "; ".join(i.message for i in warns))
                cell.setForeground(QBrush(COLOR_WARN))
            else:
                cell.setText("✓ in Ordnung")
                cell.setForeground(QBrush(COLOR_OK))
            cell.setToolTip(cell.text())
        self._loading = False
        self.issues.clear()
        for issue in sorted(res.issues, key=lambda i: {ERROR: 0, WARNING: 1, INFO: 2}[i.level]):
            prefix = {ERROR: "✗ Fehler", WARNING: "! Hinweis", INFO: "i Info"}[issue.level]
            it = QListWidgetItem(f"{prefix}{f' ({issue.op_id})' if issue.op_id else ''}: {issue.message}")
            it.setData(Qt.UserRole, issue.op_id)
            it.setForeground(QBrush(COLOR_ERROR if issue.level == ERROR else COLOR_WARN if issue.level == WARNING
                                    else QColor(128, 128, 128)))
            self.issues.addItem(it)
        self.issues.setVisible(bool(res.issues))
        accepted = len(plan.accepted_operations())
        c = res.counts()
        text = (f"<b>{accepted} von {len(plan.operations)} Änderungen übernommen</b> · "
                f"{c['renamed']} umbenannt · {c['moved'] + c['moved_renamed']} verschoben · {c['new']} neue Ordner")
        if res.has_errors:
            text += f"<br><b>{len(res.errors)} Konflikt(e)</b> – bitte Haken entfernen oder Zielnamen anpassen."
            self.summary.set_text(text, "error")
        elif accepted == 0:
            self.summary.set_text(text + "<br>Es ist keine Änderung ausgewählt.", "warn")
        else:
            self.summary.set_text(text + "<br>Keine Konflikte gefunden.", "ok")
        self.win.update_nav()

    def _issue_clicked(self, item: QListWidgetItem) -> None:
        op_id = item.data(Qt.UserRole)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.UserRole) == op_id:
                self.table.selectRow(r)
                self.table.scrollToItem(self.table.item(r, 0))
                return

    def _set_all(self, value: bool) -> None:
        for op in self.session.plan.operations:
            op.accepted = value and op.executable
        self._fill_table()
        self.revalidate()

    def _only_safe(self) -> None:
        for op in self.session.plan.operations:
            op.accepted = op.executable and (op.confidence is None or op.confidence >= 0.7)
        self._fill_table()
        self.revalidate()

    # -- eigene Änderungen ----------------------------------------------------
    def _context_menu(self, pos) -> None:
        rel = self.before.selected_path()
        if rel is None:
            return
        menu = QMenu(self)
        menu.addAction("Umbenennen oder verschieben …", lambda: self._edit_entry(rel))
        entry = self.session.snapshot.get(rel)
        if entry is not None and entry.is_dir:
            menu.addAction("Neuen Unterordner anlegen …", lambda: self._add_folder(rel))
        menu.exec(self.before.view.viewport().mapToGlobal(pos))

    def _add_for_selected(self) -> None:
        rel = self.before.selected_path()
        if rel is None:
            QMessageBox.information(self, "Eintrag wählen", "Bitte zuerst links im Original eine Datei oder einen "
                                                            "Ordner auswählen.")
            return
        self._edit_entry(rel)

    def _edit_entry(self, rel: str) -> None:
        snap, plan = self.session.snapshot, self.session.plan
        entry = snap.get(rel)
        if entry is None or entry.is_link:
            return
        res = self.session.resolution
        existing = next((op for op in plan.operations if op.source and op.source.casefold() == rel.casefold()
                         and op.executable), None)
        current = existing.target if existing else (res.resolve_any(rel) if res else rel)
        dlg = EditTargetDialog(rel, current, entry.is_dir, self)
        if not dlg.exec():
            return
        try:
            target = normalize_rel_path(dlg.target())
        except PathError as exc:
            QMessageBox.warning(self, "Ungültiger Pfad", str(exc))
            return
        if not target:
            return
        if existing:
            existing.target = target
            existing.accepted = True
            existing.modified = True
        else:
            same_parent = parent_of(target).casefold() == parent_of(rel).casefold()
            plan.operations.append(Operation(
                id=plan.next_id(), type=RENAME if same_parent else MOVE, source=entry.rel_path, target=target,
                reason="Eigene Änderung", confidence=1.0, origin="benutzer", modified=True,
            ))
        self._fill_table()
        self.revalidate()

    def _add_folder(self, base_rel: str | None = None) -> None:
        res = self.session.resolution
        base = res.resolve_any(base_rel) if (res and base_rel) else (base_rel or "")
        dlg = EditTargetDialog("", join_rel(base, "Neuer_Ordner"), True, self, create_folder=True)
        if not dlg.exec():
            return
        try:
            target = normalize_rel_path(dlg.target())
        except PathError as exc:
            QMessageBox.warning(self, "Ungültiger Pfad", str(exc))
            return
        if not target:
            return
        plan = self.session.plan
        plan.operations.append(Operation(id=plan.next_id(), type=CREATE_FOLDER, target=target,
                                         reason="Eigene Änderung", confidence=1.0, origin="benutzer", modified=True))
        self._fill_table()
        self.revalidate()

    def _save_plan(self) -> None:
        name = f"Organisationsplan_{self.session.snapshot.root_name}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Plan speichern", str(desktop_dir() / name), "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.session.plan.to_json())

    # -- Ausführen ----------------------------------------------------------
    def next_label(self) -> str:
        return "Kopie erstellen …"

    def next_enabled(self) -> bool:
        res = self.session.resolution
        return (res is not None and not res.has_errors and self.session.plan is not None
                and bool(self.session.plan.accepted_operations()))

    def on_next(self) -> None:
        self.timer.stop()
        self.revalidate()
        if not self.next_enabled():
            return
        snap, plan, res = self.session.snapshot, self.session.plan, self.session.resolution
        lang = plan.language or ("en" if self.session.settings.rules.language == "en" else "de")
        prep = prepare_run(snap, res, dest_parent=self.session.settings.output_dir or None, lang=lang)
        dlg = ConfirmExecutionDialog(prep, res.counts(), len(plan.accepted_operations()), self)
        if not dlg.exec() or not prep.ok:
            return
        try:
            self.session.run = create_run(prep, snap, plan, res, lang=lang)
        except Exception as exc:
            QMessageBox.warning(self, "Vorgang kann nicht starten", str(exc))
            return
        self.win.go_next()

