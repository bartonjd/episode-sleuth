#!/usr/bin/env python3
"""Library management page: view, search, edit and delete reference episodes.

Lists every episode stored in the current fingerprint database and lets the user
correct its metadata (show title, episode title, season, episode, expected
duration) or remove wrong / duplicate entries outright. All changes go straight
to the same SQLite database the Build and Identify pages use, via the
:class:`FingerprintDB` CRUD helpers (``list_media`` / ``update_media`` /
``delete_media``).
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView, QAbstractItemView,
    QTableWidgetItem,
)

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, LineEdit, TableWidget,
    InfoBar, InfoBarPosition, TitleLabel, CaptionLabel, MessageBox,
    MessageBoxBase, SubtitleLabel, BodyLabel, SpinBox,
)

# Core database helpers, imported defensively so the page still loads even if the
# heavy engine dependencies are unavailable (mirrors settings.py).
try:
    from fingerprint_core import FingerprintDB
except Exception:  # pragma: no cover - only if deps are absent
    FingerprintDB = None


# Table columns (media_id is stashed on the first column via Qt.UserRole).
_COLS = ["Show", "Episode title", "Type", "Season", "Episode", "Duration",
         "Fingerprints"]


class EditEpisodeDialog(MessageBoxBase):
    """A Fluent dialog to edit one reference episode's metadata."""

    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self._row = row
        self.titleLabel = SubtitleLabel("Edit reference episode", self)
        self.viewLayout.addWidget(self.titleLabel)

        def _field(label_text: str, value) -> LineEdit:
            self.viewLayout.addWidget(BodyLabel(label_text, self))
            le = LineEdit(self)
            le.setText("" if value is None else str(value))
            le.setClearButtonEnabled(True)
            self.viewLayout.addWidget(le)
            return le

        # Show / episode title are free text. Season / episode are integers via
        # spin boxes (0 = "unset"). Duration is entered in whole minutes.
        self.show_edit = _field("Show title", row.get("show_title") or "")
        self.title_edit = _field("Episode title", row.get("episode_title") or "")
        self.series_title_edit = _field(
            "Series/media title (internal)", row.get("title") or "")

        self.viewLayout.addWidget(BodyLabel("Season", self))
        self.season_spin = SpinBox(self)
        self.season_spin.setRange(0, 99)
        self.season_spin.setValue(int(row.get("season") or 0))
        self.viewLayout.addWidget(self.season_spin)

        self.viewLayout.addWidget(BodyLabel("Episode", self))
        self.episode_spin = SpinBox(self)
        self.episode_spin.setRange(0, 999)
        self.episode_spin.setValue(int(row.get("episode") or 0))
        self.viewLayout.addWidget(self.episode_spin)

        self.viewLayout.addWidget(
            BodyLabel("Expected duration (minutes, 0 = unknown)", self))
        self.duration_spin = SpinBox(self)
        self.duration_spin.setRange(0, 600)
        dur_sec = row.get("duration_seconds")
        self.duration_spin.setValue(
            int(round(dur_sec / 60.0)) if dur_sec else 0)
        self.viewLayout.addWidget(self.duration_spin)

        self.yesButton.setText("Save")
        self.cancelButton.setText("Cancel")
        self.widget.setMinimumWidth(460)

    def values(self) -> dict:
        """Return the edited values as a dict of media columns to update.

        Empty spin values (0) map to ``None`` for season/episode/duration so
        "unset" is preserved rather than being stored as a literal zero.
        """
        season = self.season_spin.value() or None
        episode = self.episode_spin.value() or None
        minutes = self.duration_spin.value() or None
        return {
            "show_title": self.show_edit.text().strip() or None,
            "episode_title": self.title_edit.text().strip() or None,
            "title": self.series_title_edit.text().strip()
            or (self._row.get("title") or ""),
            "season": season,
            "episode": episode,
            "duration_seconds": (minutes * 60) if minutes else None,
            # A hand-entered runtime is authoritative (like the TVMaze lookup),
            # so the matcher trusts it for the hard duration-mismatch check.
            # Clearing the duration also clears its source.
            "duration_source": "manual" if minutes else None,
        }


class LibraryInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.cfg = window.gui_cfg
        self.setObjectName("libraryInterface")
        self._all_rows: List[dict] = []  # every row loaded from the DB

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        root.addWidget(TitleLabel("Manage reference library"))
        root.addWidget(CaptionLabel(
            "View, search, edit or delete the episodes stored in the current "
            "fingerprint database. Edits and deletions are saved immediately."))

        # Search + action row
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search_edit = LineEdit()
        self.search_edit.setPlaceholderText(
            "Search by show, episode title, or season/episode...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.search_edit, 1)

        self.refresh_btn = PushButton("Refresh", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.reload)
        top.addWidget(self.refresh_btn)

        self.edit_btn = PushButton("Edit", self, FIF.EDIT)
        self.edit_btn.clicked.connect(self._edit_selected)
        top.addWidget(self.edit_btn)

        self.delete_btn = PrimaryPushButton("Delete", self, FIF.DELETE)
        self.delete_btn.clicked.connect(self._delete_selected)
        top.addWidget(self.delete_btn)
        root.addLayout(top)

        # Table
        self.table = TableWidget(self)
        self.table.setColumnCount(len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, len(_COLS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _idx: self._edit_selected())
        root.addWidget(self.table, 1)

        self.count_label = CaptionLabel("")
        root.addWidget(self.count_label)

        # Populate on first construction so the tab is not empty.
        self.reload()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _open_db(self) -> Optional["FingerprintDB"]:
        if FingerprintDB is None:
            InfoBar.error(
                "Unavailable",
                "The database engine could not be loaded, so the library "
                "cannot be managed.",
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            return None
        path = self.win.current_db_path()
        if not path:
            InfoBar.warning(
                "No database",
                "Set a fingerprint database path in Settings first.",
                duration=5000, position=InfoBarPosition.TOP, parent=self)
            return None
        try:
            return FingerprintDB(path)
        except Exception as exc:
            InfoBar.error(
                "Could not open database", str(exc),
                duration=8000, position=InfoBarPosition.TOP, parent=self)
            return None

    def reload(self):
        """Reload every media row from the current database into memory."""
        db = self._open_db()
        if db is None:
            self._all_rows = []
            self._render([])
            return
        try:
            rows = db.list_media()
            counts = db.media_fingerprint_counts()
        except Exception as exc:
            InfoBar.error(
                "Load failed", str(exc),
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            self._all_rows = []
            self._render([])
            return
        finally:
            db.close()

        self._all_rows = []
        for r in rows:
            keys = r.keys()
            d = {k: r[k] for k in keys}
            d["_fp_count"] = counts.get(r["id"], 0)
            self._all_rows.append(d)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Filtering + rendering
    # ------------------------------------------------------------------
    def _apply_filter(self):
        q = self.search_edit.text().strip().lower()
        if not q:
            self._render(self._all_rows)
            return
        filtered = []
        for d in self._all_rows:
            hay = " ".join(str(x) for x in (
                d.get("show_title") or "", d.get("episode_title") or "",
                d.get("title") or "",
                f"s{d.get('season') or ''}", f"e{d.get('episode') or ''}",
                f"s{d.get('season') or 0:02d}e{d.get('episode') or 0:02d}"
                if d.get("season") and d.get("episode") else "",
            )).lower()
            if q in hay:
                filtered.append(d)
        self._render(filtered)

    def _render(self, rows: List[dict]):
        self.table.setRowCount(0)
        for d in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            dur = d.get("duration_seconds")
            dur_str = f"{int(dur)//60} min" if dur else "-"
            values = [
                d.get("show_title") or d.get("title") or "-",
                d.get("episode_title") or "-",
                d.get("media_type") or "-",
                "" if d.get("season") is None else str(d.get("season")),
                "" if d.get("episode") is None else str(d.get("episode")),
                dur_str,
                str(d.get("_fp_count", 0)),
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                if c == 0:
                    # Stash the media id on the first cell for later lookup.
                    item.setData(Qt.UserRole, d.get("id"))
                if c >= 2:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)
        self.count_label.setText(
            f"Showing {len(rows)} of {len(self._all_rows)} reference episodes.")

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _selected_media_id(self) -> Optional[int]:
        items = self.table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        cell = self.table.item(row, 0)
        if cell is None:
            return None
        return cell.data(Qt.UserRole)

    def _row_by_id(self, media_id) -> Optional[dict]:
        for d in self._all_rows:
            if d.get("id") == media_id:
                return d
        return None

    # ------------------------------------------------------------------
    # Edit / delete
    # ------------------------------------------------------------------
    def _edit_selected(self):
        media_id = self._selected_media_id()
        if media_id is None:
            self._info("Select an episode to edit first.")
            return
        row = self._row_by_id(media_id)
        if row is None:
            self._info("Could not find the selected episode; try Refresh.")
            return
        dlg = EditEpisodeDialog(self.win, row)
        if not dlg.exec():
            return
        updates = dlg.values()
        db = self._open_db()
        if db is None:
            return
        try:
            db.update_media(media_id, **updates)
        except Exception as exc:
            InfoBar.error(
                "Update failed", str(exc),
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            return
        finally:
            db.close()
        InfoBar.success(
            "Episode updated", "The reference entry was saved.",
            duration=4000, position=InfoBarPosition.TOP, parent=self)
        self.reload()

    def _delete_selected(self):
        media_id = self._selected_media_id()
        if media_id is None:
            self._info("Select an episode to delete first.")
            return
        row = self._row_by_id(media_id)
        label = "this episode"
        if row is not None:
            show = row.get("show_title") or row.get("title") or ""
            se = ""
            if row.get("season") and row.get("episode"):
                se = f" S{int(row['season']):02d}E{int(row['episode']):02d}"
            label = f"{show}{se}".strip() or label
        box = MessageBox(
            "Delete reference episode",
            f"Delete {label} and all of its fingerprints from the database?\n\n"
            "This cannot be undone.",
            self.win)
        box.yesButton.setText("Delete")
        box.cancelButton.setText("Cancel")
        if not box.exec():
            return
        db = self._open_db()
        if db is None:
            return
        try:
            db.delete_media(media_id)
        except Exception as exc:
            InfoBar.error(
                "Delete failed", str(exc),
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            return
        finally:
            db.close()
        InfoBar.success(
            "Episode deleted", "The reference entry was removed.",
            duration=4000, position=InfoBarPosition.TOP, parent=self)
        self.reload()

    def _info(self, msg: str):
        InfoBar.info("Manage library", msg, duration=4000,
                     position=InfoBarPosition.TOP, parent=self)
