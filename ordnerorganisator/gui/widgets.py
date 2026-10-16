"""Wiederverwendbare Bausteine der Oberfläche."""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..filetypes import category_by_key
from ..models import Entry, Snapshot, format_size
from ..planner import (
    INHERITED,
    MERGED,
    MOVED,
    MOVED_RENAMED,
    NEW,
    RENAMED,
    STATUS_LABELS_DE,
    UNCHANGED,
    FinalNode,
    Resolution,
)

PATH_ROLE = Qt.UserRole + 1

STATUS_COLORS = {
    NEW: "#2e9d4f",
    RENAMED: "#2f74c0",
    MOVED: "#c77d14",
    MOVED_RENAMED: "#b5528f",
    MERGED: "#8a6d3b",
}


def heading(text: str, size: int = 15) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setPointSize(size)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setWordWrap(True)
    return lbl


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    pal = lbl.palette()
    c = pal.color(lbl.foregroundRole())
    c.setAlpha(170)
    pal.setColor(lbl.foregroundRole(), c)
    lbl.setPalette(pal)
    return lbl


class InfoBox(QFrame):
    """Farbiger Hinweisrahmen (info, warn, error, ok)."""

    COLORS = {
        "info": ("#2f74c0", "rgba(47,116,192,0.10)"),
        "warn": ("#c77d14", "rgba(199,125,20,0.12)"),
        "error": ("#c0392b", "rgba(192,57,43,0.12)"),
        "ok": ("#2e9d4f", "rgba(46,157,79,0.12)"),
    }

    def __init__(self, text: str = "", kind: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(self.label)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        border, bg = self.COLORS.get(kind, self.COLORS["info"])
        self.setStyleSheet(
            f"InfoBox {{ border-left: 4px solid {border}; background: {bg}; border-radius: 4px; }}"
        )

    def set_text(self, text: str, kind: str | None = None) -> None:
        self.label.setText(text)
        if kind:
            self.set_kind(kind)


def html_list(items: list[str], limit: int = 30) -> str:
    from html import escape

    shown = "".join(f"<li>{escape(x)}</li>" for x in items[:limit])
    more = f"<li>… und {len(items) - limit} weitere</li>" if len(items) > limit else ""
    return f"<ul style='margin:2px 0 2px -20px'>{shown}{more}</ul>"


class TreeFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.setAutoAcceptChildRows(True)


def _icons():
    style = QApplication.style()
    return style.standardIcon(QStyle.SP_DirIcon), style.standardIcon(QStyle.SP_FileIcon), style.standardIcon(
        QStyle.SP_FileLinkIcon
    )


def build_snapshot_model(snapshot: Snapshot, show_hidden: bool = True) -> QStandardItemModel:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name", "Typ", "Größe", "Relativer Pfad"])
    dir_icon, file_icon, link_icon = _icons()

    def add(parent_item, entry: Entry) -> None:
        for c in entry.children:
            if not show_hidden and c.is_special:
                continue
            name = QStandardItem(link_icon if c.is_link else (dir_icon if c.is_dir else file_icon), c.name)
            name.setData(c.rel_path, PATH_ROLE)
            name.setEditable(False)
            if c.is_dir:
                kind = "Verknüpfung" if c.is_link else f"Ordner ({c.file_count} Dateien)"
                size = format_size(c.total_size)
            else:
                kind = "Verknüpfung" if c.is_link else (
                    f"{category_by_key(c.category).label('de')} ({c.ext})" if c.ext else "Datei ohne Endung")
                size = format_size(c.size)
            row = [name, QStandardItem(kind), QStandardItem(size), QStandardItem(c.rel_path)]
            for it in row[1:]:
                it.setEditable(False)
            if c.is_special or c.is_link:
                for it in row:
                    it.setForeground(QBrush(QColor(128, 128, 128)))
                    f = it.font()
                    f.setItalic(True)
                    it.setFont(f)
            if c.error:
                row[1].setText(f"Fehler: {c.error}")
            parent_item.appendRow(row)
            if c.is_dir and c.children:
                add(name, c)

    add(model.invisibleRootItem(), snapshot.root)
    return model


def build_final_model(resolution: Resolution) -> QStandardItemModel:
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name", "Änderung", "Herkunft"])
    dir_icon, file_icon, _ = _icons()

    def add(parent_item, node: FinalNode) -> None:
        for c in node.children:
            name = QStandardItem(dir_icon if c.is_dir else file_icon, c.name)
            name.setData(c.rel_path, PATH_ROLE)
            status = QStandardItem("" if c.status in (UNCHANGED, INHERITED) else STATUS_LABELS_DE[c.status])
            origin = QStandardItem(", ".join(c.origins) if c.status not in (UNCHANGED, INHERITED, NEW) else "")
            for it in (name, status, origin):
                it.setEditable(False)
            color = STATUS_COLORS.get(c.status)
            if color:
                for it in (name, status):
                    it.setForeground(QBrush(QColor(color)))
                f = name.font()
                f.setBold(True)
                name.setFont(f)
            parent_item.appendRow([name, status, origin])
            if c.is_dir:
                add(name, c)

    add(model.invisibleRootItem(), resolution.root)
    return model


def make_tree_view() -> QTreeView:
    view = QTreeView()
    view.setAlternatingRowColors(True)
    view.setUniformRowHeights(True)
    view.setSortingEnabled(False)
    view.setEditTriggers(QTreeView.NoEditTriggers)
    return view


class SearchableTree(QWidget):
    """Baumansicht mit Suchfeld."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if title:
            t = QLabel(title)
            f = QFont(t.font())
            f.setBold(True)
            t.setFont(f)
            lay.addWidget(t)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Typ oder Pfad) …")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.view = make_tree_view()
        lay.addWidget(self.view, 1)
        self.proxy = TreeFilterProxy(self)
        self.view.setModel(self.proxy)
        self.search.textChanged.connect(self._filter)

    def _filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text.strip())
        if text.strip():
            self.view.expandAll()

    def set_model(self, model: QStandardItemModel, expand_depth: int = 0) -> None:
        old = self.proxy.sourceModel()
        self.proxy.setSourceModel(model)
        if old is not None and old is not model:
            old.deleteLater()
        self.view.expandToDepth(expand_depth)
        self.view.header().setStretchLastSection(True)
        self.view.setColumnWidth(0, 300)
        if self.search.text().strip():
            self._filter(self.search.text())

    def selected_path(self) -> str | None:
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None
        src = self.proxy.mapToSource(idx.siblingAtColumn(0))
        return self.proxy.sourceModel().itemFromIndex(src).data(PATH_ROLE)


def legend() -> QLabel:
    parts = [
        f"<span style='color:{STATUS_COLORS[NEW]}'><b>■ neu</b></span>",
        f"<span style='color:{STATUS_COLORS[RENAMED]}'><b>■ umbenannt</b></span>",
        f"<span style='color:{STATUS_COLORS[MOVED]}'><b>■ verschoben</b></span>",
        f"<span style='color:{STATUS_COLORS[MOVED_RENAMED]}'><b>■ verschoben + umbenannt</b></span>",
        f"<span style='color:{STATUS_COLORS[MERGED]}'><b>■ zusammengeführt</b></span>",
    ]
    lbl = QLabel(" &nbsp; ".join(parts))
    lbl.setTextFormat(Qt.RichText)
    return lbl
