#!/usr/bin/env python3
"""Identify page: match a folder of DVD rips to their episodes."""
from __future__ import annotations

import logging
import os
import shutil
from types import SimpleNamespace
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..main_window import MainWindow

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    DoubleSpinBox,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    MessageBoxBase,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SearchLineEdit,
    SegmentedWidget,
    SpinBox,
    StateToolTip,
    SubtitleLabel,
    TableWidget,
    TextEdit,
    TitleLabel,
    ToolButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from engine import (
    FileResult,
    episode_id_str,
    titles_equivalent,
    write_csv,
    write_json,
)

# Filename title parser (re-exported by the engine; may be None if the optional
# subtitle_utils dependency is unavailable - used only for the mismatch badge).
try:
    from engine.discovery import parse_episode_info
except Exception:  # pragma: no cover - defensive
    parse_episode_info = None

# Speech-to-text helpers are imported defensively so the page still loads even
# when the optional STT deps (pydub, vosk) are absent - it is only used to check
# whether the Vosk model is present before an identify run.
try:
    import stt_utils
except Exception:  # pragma: no cover - only if deps are missing
    stt_utils = None

from .. import rename_history
from ..constants import (
    COLOR_MEDIUM,
    COLOR_OK,
    COLOR_REVIEW,
    HERE,
    LEGEND_MEDIUM,
    LEGEND_OK,
    LEGEND_REVIEW,
    PILL_HIGH,
    PILL_LOW,
    PILL_MED,
)
from ..widgets import Card, CollapsibleCard, _path_row
from ..workers import IdentifyWorker

# Order of the status filter tabs and the categories they map to.
STATUS_TABS = ["All", "Rename", "Correct", "Review"]

# View-density presets. Each entry drives the page's spacing, the card content
# margins/spacing, the results-table row height, and the page-header font size.
# Keys: root_margin (l, t, r, b), root_spacing, card_margin (l, t, r, b),
# card_spacing, row_height (px), header_px (pixel size for the title label).
# The stock TitleLabel is 28px; every preset here is <= 24px so the header is
# reduced in size (and paired with tighter spacing) at all densities.
DENSITY_SPECS = {
    "Compact": {
        "root_margin": (16, 10, 16, 10),
        "root_spacing": 8,
        "card_margin": (14, 8, 14, 8),
        "card_spacing": 6,
        "row_height": 26,
        "header_px": 18,
    },
    "Standard": {
        "root_margin": (28, 16, 28, 20),
        "root_spacing": 12,
        "card_margin": (20, 14, 20, 14),
        "card_spacing": 10,
        "row_height": 34,
        "header_px": 22,
    },
    "Comfortable": {
        "root_margin": (36, 26, 36, 26),
        "root_spacing": 18,
        "card_margin": (26, 20, 26, 20),
        "card_spacing": 14,
        "row_height": 44,
        "header_px": 24,
    },
}


class IdentifyInterface(QWidget):
    COLS = ["", "File", "Status", "Episode", "Episode Title",
            "Suggested Name", "Match %", "Samples Agree", "Notes"]
    # column indices (kept in one place so row population and filtering stay in sync)
    C_CHECK, C_FILE, C_STATUS, C_EPISODE, C_TITLE = 0, 1, 2, 3, 4
    C_SUGGESTED, C_MATCH, C_AGREE, C_NOTES = 5, 6, 7, 8

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.cfg = window.gui_cfg
        self.setObjectName("identifyInterface")
        self.results: List[FileResult] = []
        self.worker: Optional[IdentifyWorker] = None
        self.state_tip: Optional[StateToolTip] = None
        # Current status filter tab (All / Rename / Correct / Review) and a
        # debounce timer so dragging a column border only writes config once.
        self._status_filter = self._ident_get("status_filter", "All")
        if self._status_filter not in STATUS_TABS:
            self._status_filter = "All"
        self._sort_col = int(self._ident_get("sort_col", -1))
        self._sort_order = int(self._ident_get("sort_order", 0))
        self._restoring_layout = True   # suppress width saves during setup
        self._width_save_timer = QTimer(self)
        self._width_save_timer.setSingleShot(True)
        self._width_save_timer.setInterval(400)
        self._width_save_timer.timeout.connect(self._persist_column_widths)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        self.root = root

        self.header_label = TitleLabel("Identify episodes")
        root.addWidget(self.header_label)
        root.addWidget(CaptionLabel(
            "Point at a folder of DVD rips and match each file to its episode. "
            "The reference database is set on the Settings page."))

        # --- source card ---
        self.src_card = src_card = Card("DVD rips to identify")
        self.source_edit = _path_row("Folder or single video/audio file")
        self.source_edit.setText(self.cfg.get("last_source", ""))
        folder_btn = PushButton("Folder", self, FIF.FOLDER)
        file_btn = PushButton("File", self, FIF.VIDEO)
        folder_btn.clicked.connect(self._pick_folder)
        file_btn.clicked.connect(self._pick_file)
        src_card.add(self.source_edit, folder_btn, file_btn)
        root.addWidget(src_card)

        # --- options card (collapsible) ---
        self.opt_card = opt_card = CollapsibleCard("Options")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)

        grid.addWidget(BodyLabel("Samples per file"), 0, 0)
        self.samples_spin = SpinBox()
        self.samples_spin.setRange(1, 15)
        self.samples_spin.setValue(int(self.cfg.get("samples_per_file", 5)))
        grid.addWidget(self.samples_spin, 1, 0)

        grid.addWidget(BodyLabel("Sample length (s)"), 0, 1)
        self.samplelen_spin = DoubleSpinBox()
        self.samplelen_spin.setRange(4.0, 30.0)
        self.samplelen_spin.setSingleStep(1.0)
        self.samplelen_spin.setValue(float(self.cfg.get("sample_length", 12.0)))
        grid.addWidget(self.samplelen_spin, 1, 1)

        grid.addWidget(BodyLabel("Review below confidence"), 0, 2)
        self.review_spin = DoubleSpinBox()
        self.review_spin.setRange(0.05, 0.95)
        self.review_spin.setSingleStep(0.05)
        self.review_spin.setValue(float(self.cfg.get("review_confidence", 0.35)))
        grid.addWidget(self.review_spin, 1, 2)

        grid.addWidget(BodyLabel("Parallel workers"), 0, 3)
        self.workers_spin = SpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(int(self.cfg.get("max_workers", 4)))
        grid.addWidget(self.workers_spin, 1, 3)

        grid.setColumnStretch(4, 1)
        opt_card.addLayout(grid)
        root.addWidget(opt_card)

        # --- action bar ---
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.identify_btn = PrimaryPushButton("Identify", self, FIF.SEARCH)
        self.identify_btn.clicked.connect(self._start)
        self.cancel_btn = PushButton("Cancel", self, FIF.CANCEL)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)
        self.progress = ProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(6)
        self.progress_pct = CaptionLabel("0%")
        self.progress_pct.setVisible(False)
        self.progress_pct.setFixedWidth(38)
        actions.addWidget(self.identify_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.progress, 1)
        actions.addWidget(self.progress_pct)

        self.preview_btn = PushButton("Preview Renames", self, FIF.VIEW)
        self.rename_btn = PushButton("Rename for Plex", self, FIF.EDIT)
        self.undo_btn = PushButton("Undo Last Rename", self, FIF.CANCEL)
        self.export_json_btn = PushButton("Export JSON", self, FIF.SAVE_AS)
        self.export_csv_btn = PushButton("Export CSV", self, FIF.SAVE)
        self.preview_btn.clicked.connect(self._preview_renames)
        self.rename_btn.clicked.connect(self._rename_plex)
        self.undo_btn.clicked.connect(self._undo_last_rename)
        self.export_json_btn.clicked.connect(self._export_json)
        self.export_csv_btn.clicked.connect(self._export_csv)
        # "Customize View" is only shown in Advanced mode (toggled by
        # apply_view_preferences); it opens a dialog to pick which optional
        # panels are visible.
        self.customize_btn = PushButton("Customize View", self, FIF.SETTING)
        self.customize_btn.setToolTip(
            "Choose which optional panels are shown on this page")
        self.customize_btn.clicked.connect(self._open_customize_view)
        actions.addWidget(self.preview_btn)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.undo_btn)
        actions.addWidget(self.export_json_btn)
        actions.addWidget(self.export_csv_btn)
        actions.addWidget(self.customize_btn)
        root.addLayout(actions)

        # --- dry-run preference (wrapped so it can be hidden by view mode) ---
        self.dry_row_widget = QWidget()
        dry_row = QHBoxLayout(self.dry_row_widget)
        dry_row.setContentsMargins(0, 0, 0, 0)
        dry_row.setSpacing(8)
        self.dry_run_check = CheckBox("Always preview renames before copying")
        self.dry_run_check.setChecked(bool(self._ident_get("dry_run_preview", False)))
        self.dry_run_check.setToolTip(
            "When ticked, 'Rename for Plex' shows the before/after list for "
            "review first instead of copying immediately.")
        self.dry_run_check.stateChanged.connect(self._on_dry_run_toggled)
        dry_row.addWidget(self.dry_run_check)
        dry_row.addStretch(1)
        root.addWidget(self.dry_row_widget)
        # The undo button is only useful once a batch has been recorded.
        self.undo_btn.setEnabled(rename_history.peek_last_batch() is not None)

        # --- filter / search box ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.filter_edit = SearchLineEdit()
        self.filter_edit.setPlaceholderText(
            "Filter results by file name, episode, or title...")
        self.filter_edit.textChanged.connect(self._filter_results)
        self.filter_edit.setClearButtonEnabled(True)
        filter_row.addWidget(BodyLabel("Filter"))
        filter_row.addWidget(self.filter_edit, 1)
        # Running tally of how many files are in the results table. Updated as
        # rows arrive and whenever the filter hides/shows rows.
        self.count_label = BodyLabel("0 items")
        filter_row.addWidget(self.count_label)
        root.addLayout(filter_row)

        # --- status filter tabs (All / Rename / Correct / Review) ---
        self.status_pivot = SegmentedWidget()
        for key in STATUS_TABS:
            self.status_pivot.addItem(routeKey=key, text=key)
        self.status_pivot.setCurrentItem(self._status_filter)
        self.status_pivot.currentItemChanged.connect(self._on_status_filter_changed)
        seg_row = QHBoxLayout()
        seg_row.addWidget(self.status_pivot)
        seg_row.addStretch(1)
        root.addLayout(seg_row)

        # --- status colour legend (wrapped so it can be hidden by view mode) ---
        self.legend_widget = QWidget()
        legend_layout = self._build_legend()
        legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_widget.setLayout(legend_layout)
        root.addWidget(self.legend_widget)

        # --- results table ---
        self.table = TableWidget()
        self.table.setColumnCount(len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        # Keep the table usable on small screens: always allow a vertical
        # scrollbar, scroll smoothly per-pixel, and never shrink below ~200px
        # while still expanding to fill the remaining page height.
        self.table.setMinimumHeight(200)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # A clear tooltip explaining what "Samples Agree" means.
        agree_hdr = self.table.horizontalHeaderItem(self.C_AGREE)
        if agree_hdr is not None:
            agree_hdr.setToolTip("Fraction of samples that agree on this episode")
        # All columns are user-resizable (Interactive) except the fixed-width
        # checkbox and the narrow Notes-icon column.
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(self.C_CHECK, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.C_NOTES, QHeaderView.Fixed)
        widths = {
            self.C_CHECK: 40, self.C_FILE: 230, self.C_STATUS: 80,
            self.C_EPISODE: 90, self.C_TITLE: 200, self.C_SUGGESTED: 230,
            self.C_MATCH: 80, self.C_AGREE: 110, self.C_NOTES: 46,
        }
        for col, w in widths.items():
            self.table.setColumnWidth(col, w)
        # Restore any column widths the user saved in a previous session, then
        # start listening for further resizes (debounced back to config).
        self._restore_column_widths()
        hdr.sectionResized.connect(self._on_section_resized)
        # Click a header to sort by that column (rebuilds rows so the cell
        # widgets - pills, buttons - move with their data). The checkbox and
        # notes-icon columns are not meaningfully sortable.
        hdr.setSortIndicatorShown(True)
        hdr.sectionClicked.connect(self._on_header_clicked)
        if 0 <= self._sort_col < len(self.COLS):
            order = (Qt.DescendingOrder if self._sort_order else Qt.AscendingOrder)
            hdr.setSortIndicator(self._sort_col, order)
        root.addWidget(self.table, 1)
        # Setup done: allow the resize handler to persist future changes.
        self._restoring_layout = False

        # Apply the saved view density / mode (spacing, panel visibility).
        self.apply_view_preferences()

    # ----- identify-page config helpers -----
    def _ident_get(self, key: str, default=None):
        """Read one value from the nested ``identify_page`` config section."""
        section = self.cfg.get("identify_page", {}) or {}
        return section.get(key, default)

    def _ident_set(self, **kwargs) -> None:
        """Merge values into the ``identify_page`` config section and save."""
        section = dict(self.cfg.get("identify_page", {}) or {})
        section.update(kwargs)
        self.cfg.set("identify_page", section)
        self.cfg.save()

    # ----- view density / mode -----
    def _custom_panels(self) -> dict:
        """Read the per-panel visibility prefs, filling any missing keys."""
        defaults = {"options": True, "legend": True,
                    "dry_run": True, "exports": True}
        saved = self._ident_get("custom_panels", {}) or {}
        return {k: bool(saved.get(k, v)) for k, v in defaults.items()}

    def apply_view_preferences(self) -> None:
        """Apply the saved view density and mode to the page.

        Density controls spacing (root margins/spacing, card padding, table
        row height, header font size). Mode controls panel visibility:
        Simple hides the optional panels for a clean layout, while Advanced
        shows them per the user's Customize View choices plus the Customize
        View button itself.
        """
        density = self.cfg.get("view_density", "Standard")
        spec = DENSITY_SPECS.get(density, DENSITY_SPECS["Standard"])
        mode = self.cfg.get("view_mode", "Advanced")

        # -- density: page spacing --
        self.root.setContentsMargins(*spec["root_margin"])
        self.root.setSpacing(spec["root_spacing"])
        for card in (self.src_card, self.opt_card):
            card.set_density(spec["card_margin"], spec["card_spacing"])

        # -- density: results-table row height --
        self.table.verticalHeader().setDefaultSectionSize(spec["row_height"])

        # -- density: header font size (pixel size; stock TitleLabel is 28px) --
        font = self.header_label.font()
        font.setPixelSize(spec["header_px"])
        self.header_label.setFont(font)

        # -- mode: panel visibility --
        if mode == "Simple":
            panels = {"options": False, "legend": False,
                      "dry_run": False, "exports": False}
            self.customize_btn.setVisible(False)
        else:
            panels = self._custom_panels()
            self.customize_btn.setVisible(True)

        self.opt_card.setVisible(panels["options"])
        self.legend_widget.setVisible(panels["legend"])
        self.dry_row_widget.setVisible(panels["dry_run"])
        self.export_json_btn.setVisible(panels["exports"])
        self.export_csv_btn.setVisible(panels["exports"])

    def _open_customize_view(self) -> None:
        """Open the Customize View dialog (Advanced mode) to pick panels."""
        dlg = CustomizeViewDialog(self._custom_panels(), self.window())
        if dlg.exec():
            self._ident_set(custom_panels=dlg.selected_panels())
            self.apply_view_preferences()

    # ----- table layout persistence -----
    def _restore_column_widths(self) -> None:
        """Apply any column widths saved in a previous session."""
        saved = self._ident_get("column_widths", {}) or {}
        for col_str, width in saved.items():
            try:
                col, w = int(col_str), int(width)
            except (TypeError, ValueError):
                continue
            if 0 <= col < self.table.columnCount() and w > 0:
                self.table.setColumnWidth(col, w)

    def _on_section_resized(self, *args) -> None:
        """A column was resized: schedule a debounced save (ignored during
        the initial programmatic setup)."""
        if self._restoring_layout:
            return
        self._width_save_timer.start()

    def _persist_column_widths(self) -> None:
        """Write the current column widths to config."""
        widths = {str(col): int(self.table.columnWidth(col))
                  for col in range(self.table.columnCount())}
        self._ident_set(column_widths=widths)

    # ----- pickers -----
    def _pick_folder(self):
        start = self.source_edit.text().strip() or self.cfg.get("last_source", "")
        p = QFileDialog.getExistingDirectory(self, "Select folder of DVD rips", start)
        if p:
            self.source_edit.setText(p)

    def _pick_file(self):
        start = self.cfg.get("last_source", "")
        p, _ = QFileDialog.getOpenFileName(
            self, "Select a video/audio file", start,
            "Media (*.mp4 *.mkv *.avi *.mov *.m4v *.mpg *.mpeg *.ts *.wmv "
            "*.flv *.webm *.m4a *.wav *.mp3 *.flac *.aac *.ogg);;All files (*.*)")
        if p:
            self.source_edit.setText(p)

    # ----- run -----
    def _start(self):
        if self.worker and self.worker.isRunning():
            return
        db_path = self.win.current_db_path()
        source = self.source_edit.text().strip()
        if not db_path or not os.path.exists(db_path):
            self._error("No reference database",
                        "Set a valid fingerprint database on the Settings page.")
            self.win.switchTo(self.win.settings_interface)
            return
        if not source or not os.path.exists(source):
            self._error("No source selected",
                        "Pick a folder or a single file to identify.")
            return

        # The Vosk speech-to-text model is required for identification. If it is
        # missing, prompt the user to download it instead of failing mid-run.
        model_size = self.cfg.get("vosk_model_size", "small")
        if stt_utils is not None and stt_utils.get_model_path(model_size) is None:
            self._prompt_model_download(model_size)
            return

        # persist the choices the user just made
        self.cfg.update(
            last_source=source,
            samples_per_file=int(self.samples_spin.value()),
            sample_length=float(self.samplelen_spin.value()),
            max_workers=int(self.workers_spin.value()),
            review_confidence=float(self.review_spin.value()),
        )
        self.cfg.save()

        n = max(1, int(self.samples_spin.value()))
        points = [0.5] if n == 1 else [round((i + 1) / (n + 1), 4) for i in range(n)]
        params = SimpleNamespace(
            config_path=self.win.current_engine_config() or None,
            points=points,
            sample_len=float(self.samplelen_spin.value()),
            max_workers=int(self.workers_spin.value()),
            review_confidence=float(self.review_spin.value()),
            runtime_tolerance=4.0,
            vosk_model_size=self.cfg.get("vosk_model_size", "small"),
            show_title=self.cfg.get("last_show_title", ""),
            media_extensions=self.cfg.get("media_extensions", "") or None,
            ignore_part_format=bool(
                self.cfg.get("ignore_part_format_differences", True)),
        )

        # Auto-collapse the Options card so the results have maximum room.
        if not self.opt_card.is_collapsed():
            self.opt_card.set_collapsed(True)

        self.table.setRowCount(0)
        self.results = []
        self._update_count()
        self._update_status_counts()
        self.identify_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress_pct.setText("0%")
        self.progress_pct.setVisible(True)

        self.state_tip = StateToolTip("Identifying", "Starting...", self.window())
        self.state_tip.move(self.state_tip.getSuitablePos())
        self.state_tip.show()

        self.worker = IdentifyWorker(db_path, source, params)
        self.worker.rowReady.connect(self._add_row)
        self.worker.progress.connect(self._on_progress)
        self.worker.finishedOk.connect(self._on_ok)
        self.worker.failed.connect(self._on_failed)
        self.worker.wasCancelled.connect(self._on_cancelled)
        self.worker.start()

    def _prompt_model_download(self, model_size: str):
        """Ask the user to download the missing Vosk model, then jump to Settings
        and kick off the download when they agree."""
        label = "large (~1.8 GB)" if model_size == "large" else "small (~39 MB)"
        box = MessageBox(
            "Vosk model not found",
            f"The {label} speech-to-text model is required to identify episodes "
            "but has not been downloaded yet.\n\nDownload it now?",
            self.window())
        box.yesButton.setText("Download")
        box.cancelButton.setText("Cancel")
        if not box.exec():
            return
        # Switch to Settings, select the needed size, and start the download.
        settings = self.win.settings_interface
        self.win.switchTo(settings)
        try:
            idx = settings._model_values.index(model_size)
            settings.model_combo.setCurrentIndex(idx)
        except (ValueError, AttributeError):
            pass
        if hasattr(settings, "_start_model_download"):
            settings._start_model_download()

    @staticmethod
    def _status_category(r: FileResult) -> str:
        """Bucket a result into one of Rename / Correct / Review.

        Mirrors :meth:`_row_appearance`: a low-confidence or unknown result is
        Review, an already-correct name is Correct, everything else is Rename.
        """
        status = r.name_status or "unknown"
        if r.needs_review or status == "unknown":
            return "Review"
        if status == "correct":
            return "Correct"
        return "Rename"

    def _row_matches_status(self, row: int) -> bool:
        """True when the result behind ``row`` belongs to the active status tab."""
        if self._status_filter == "All":
            return True
        chk = self.table.item(row, self.C_CHECK)
        if chk is None:
            return True
        idx = chk.data(Qt.UserRole)
        if idx is None or idx >= len(self.results):
            return True
        return self._status_category(self.results[idx]) == self._status_filter

    def _filter_results(self, text: str = ""):
        """Public slot for the search box; delegates to the combined filter."""
        self._apply_filters()

    def _on_status_filter_changed(self, key: str):
        """A status tab was clicked: remember it, persist it, re-filter."""
        self._status_filter = key if key in STATUS_TABS else "All"
        self._ident_set(status_filter=self._status_filter)
        self._apply_filters()

    def _apply_filters(self):
        """Hide rows that fail either the text search or the status tab.

        The text query is matched against the file name, episode id, episode
        title and suggested name; the status tab restricts to one naming
        category. A row is visible only when it passes both.
        """
        q = self.filter_edit.text().strip().lower()
        for row in range(self.table.rowCount()):
            visible = self._row_matches_status(row)
            if visible and q:
                hay = []
                for col in (self.C_FILE, self.C_EPISODE,
                            self.C_TITLE, self.C_SUGGESTED):
                    it = self.table.item(row, col)
                    if it is not None:
                        hay.append(it.text().lower())
                visible = q in " ".join(hay)
            self.table.setRowHidden(row, not visible)
        self._update_count()

    def _update_status_counts(self):
        """Refresh the per-status counts shown on the filter tabs."""
        counts = {"Rename": 0, "Correct": 0, "Review": 0}
        for r in self.results:
            counts[self._status_category(r)] += 1
        total = len(self.results)
        labels = {
            "All": f"All ({total})",
            "Rename": f"Rename ({counts['Rename']})",
            "Correct": f"Correct ({counts['Correct']})",
            "Review": f"Review ({counts['Review']})",
        }
        for key, text in labels.items():
            item = self.status_pivot.widget(key)
            if item is not None:
                item.setText(text)

    def _update_count(self):
        """Refresh the entry tally. Shows the visible/total split while a filter
        is active, otherwise just the total number of scanned files."""
        total = self.table.rowCount()
        visible = sum(1 for row in range(total)
                      if not self.table.isRowHidden(row))
        if visible != total:
            self.count_label.setText(f"{visible} of {total} items")
        else:
            self.count_label.setText(f"{total} item" + ("" if total == 1 else "s"))

    def _show_notes(self, filename: str, notes: str):
        box = MessageBox("Notes", f"{filename}\n\n{notes or 'No notes.'}",
                         self.window())
        box.cancelButton.setVisible(False)
        box.yesButton.setText("Close")
        box.exec()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            if self.state_tip:
                self.state_tip.setContent("Cancelling...")

    def _on_progress(self, cur: int, total: int, pct: int, name: str):
        self.progress.setRange(0, total)
        self.progress.setValue(cur)
        self.progress_pct.setText(f"{pct}%")
        if self.state_tip:
            self.state_tip.setContent(f"{cur}/{total} ({pct}%)  -  {name}")

    @staticmethod
    def _legend_swatch(colour: str, text: str, tooltip: str) -> QWidget:
        """One colour swatch + label pair for the status legend."""
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(
            f"background-color: {colour}; border-radius: 6px;")
        lbl = CaptionLabel(text)
        wrap = QWidget()
        wrap.setToolTip(tooltip)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(dot)
        lay.addWidget(lbl)
        return wrap

    def _build_legend(self) -> QHBoxLayout:
        """A compact key explaining that row colours indicate naming status
        (not match confidence)."""
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(CaptionLabel("Row colour:"))
        row.addWidget(self._legend_swatch(
            LEGEND_OK, "Correct",
            "Green: the file is already named correctly - no rename needed."))
        row.addWidget(self._legend_swatch(
            LEGEND_MEDIUM, "Rename",
            "Amber: the season/episode number differs - this file will be "
            "renamed."))
        row.addWidget(self._legend_swatch(
            LEGEND_REVIEW, "Review",
            "Red: low confidence or ambiguous - check before acting."))
        note = CaptionLabel(
            "(colours show naming status, not the match % confidence)")
        note.setObjectName("legendNote")
        row.addWidget(note)
        row.addStretch(1)
        return row

    # ---- row appearance / small builders (kept tiny and testable) --------
    @staticmethod
    def _row_appearance(r: FileResult, status: str) -> SimpleNamespace:
        """Map a result's naming verdict to its row tint, label and auto-check.

        The colour is driven by the naming verdict - the whole point of the
        tool - not by the match confidence:
            correct -> green, rename -> amber (action needed), unknown -> red.
        """
        if r.needs_review or status == "unknown":
            return SimpleNamespace(tint=COLOR_REVIEW, text="Review",
                                   auto_check=False)
        if status == "correct":
            return SimpleNamespace(tint=COLOR_OK, text="Correct",
                                   auto_check=True)
        return SimpleNamespace(tint=COLOR_MEDIUM, text="Rename",
                               auto_check=True)

    @staticmethod
    def _make_text_item(text: str, *, center: bool = False,
                        tooltip: Optional[str] = None) -> QTableWidgetItem:
        """Build a plain, non-editable table cell."""
        item = QTableWidgetItem(str(text))
        if center:
            item.setTextAlignment(Qt.AlignCenter)
        if tooltip:
            item.setToolTip(tooltip)
        return item

    @staticmethod
    def _make_confidence_pill(conf: float) -> QWidget:
        """A compact, colour-coded pill showing the match confidence percent.

        Red (<40%), amber (<70%) or green (>=70%) so the reliability of the
        audio match is readable at a glance, separate from the naming status
        that tints the whole row.
        """
        if conf >= 0.70:
            colour = PILL_HIGH
        elif conf >= 0.40:
            colour = PILL_MED
        else:
            colour = PILL_LOW
        pill = QLabel(f"{conf:.0%}")
        pill.setAlignment(Qt.AlignCenter)
        pill.setStyleSheet(
            f"background-color: {colour}; color: white; border-radius: 8px; "
            "padding: 1px 8px; font-weight: 600;")
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.addWidget(pill)
        return wrap

    def _make_notes_button(self, filename: str, notes: str) -> QWidget:
        """A centered info (i) button that opens the full note text."""
        btn = ToolButton(FIF.INFO)
        btn.setFixedSize(26, 26)
        btn.setToolTip("Show notes")
        btn.clicked.connect(
            lambda _=False, fn=filename, nt=notes: self._show_notes(fn, nt))
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(btn)
        lay.addStretch(1)
        return wrap

    def _title_mismatch(self, r: FileResult, data: dict) -> bool:
        """True when the identified episode title differs from the title in the
        on-disk filename, even though the S/E numbers line up.

        This flags likely swapped or mislabelled rips: the audio was matched to
        an episode whose name is not the one the filename claims.
        """
        if parse_episode_info is None:
            return False
        identified = (data.get("episode_title") or "").strip()
        if not identified:
            return False
        try:
            _s, _e, file_title = parse_episode_info(r.filename)
        except Exception:  # pragma: no cover - defensive
            return False
        if not file_title or not str(file_title).strip():
            return False
        ignore_pf = bool(self.cfg.get("ignore_part_format_differences", True))
        return not titles_equivalent(str(file_title), identified,
                                     ignore_part_format=ignore_pf)

    def _add_row(self, r: FileResult):
        """Append a freshly identified result and render it as a new row."""
        self.results.append(r)
        self._render_row(r, len(self.results) - 1)
        self._update_status_counts()
        # Re-apply the current filter so newly added rows respect it.
        self._apply_filters()

    def _render_row(self, r: FileResult, idx: int,
                    checked: Optional[bool] = None):
        """Render result ``r`` (at position ``idx`` in ``self.results``) as a new
        table row. When ``checked`` is None the auto-check heuristic is used;
        otherwise the given check state is restored (used when rebuilding)."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        data = r.to_row()
        conf = data["confidence"]
        status = data.get("name_status", "unknown")
        appear = self._row_appearance(r, status)
        notes = data["notes"] or ("ok" if not r.needs_review else "review")

        # checkbox column (stores the result index in UserRole)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        want = appear.auto_check if checked is None else checked
        chk.setCheckState(Qt.Checked if want else Qt.Unchecked)
        chk.setData(Qt.UserRole, idx)
        self.table.setItem(row, self.C_CHECK, chk)

        # For correctly-named files the suggested name equals the current name;
        # show a dash to keep the column uncluttered. Otherwise show the target.
        suggested = data.get("suggested_filename", "")
        suggested_disp = "-" if status == "correct" else (suggested or "-")

        # Populate the text columns via the small item builder.
        self.table.setItem(row, self.C_FILE,
                           self._make_text_item(data["filename"]))
        self.table.setItem(row, self.C_STATUS,
                           self._make_text_item(appear.text, center=True))
        self.table.setItem(row, self.C_EPISODE,
                           self._make_text_item(data["episode_id"], center=True))
        self.table.setItem(row, self.C_AGREE,
                           self._make_text_item(data["agreement"], center=True))

        # Episode Title - flag a swapped/mismatched title with a badge icon.
        title_item = self._make_text_item(data.get("episode_title", ""))
        if self._title_mismatch(r, data):
            # Render the flag in an explicit high-contrast red so it stays
            # visible on tinted status rows (the theme default can wash out).
            title_item.setIcon(FIF.FLAG.icon(color=QColor("#d32f2f")))
            title_item.setToolTip(
                "Identified title differs from the title in the filename - "
                "the audio may be mislabelled or swapped. Check before renaming.")
        self.table.setItem(row, self.C_TITLE, title_item)

        # Suggested name - centered dash + explanatory tooltip when correct.
        sugg_item = self._make_text_item(
            suggested_disp,
            center=(suggested_disp == "-"),
            tooltip=("Already correctly named - no rename needed."
                     if status == "correct" else None))
        self.table.setItem(row, self.C_SUGGESTED, sugg_item)

        # Match % - a placeholder item carries the row tint; the pill sits on top.
        self.table.setItem(row, self.C_MATCH, QTableWidgetItem())
        self.table.setCellWidget(row, self.C_MATCH,
                                 self._make_confidence_pill(conf))

        # Apply the row tint to every text cell.
        brush = QBrush(appear.tint)
        for col in range(self.table.columnCount()):
            it = self.table.item(row, col)
            if it is not None:
                it.setBackground(brush)

        # Notes column: a compact info (i) button.
        self.table.setCellWidget(row, self.C_NOTES,
                                 self._make_notes_button(data["filename"], notes))

    # ----- sorting (rebuilds rows so cell widgets follow their data) -----
    def _on_header_clicked(self, col: int):
        """Sort by the clicked column, toggling direction on repeat clicks.

        The checkbox and notes-icon columns carry no sortable data, so clicking
        them is ignored.
        """
        if col in (self.C_CHECK, self.C_NOTES) or not self.results:
            return
        if col == self._sort_col:
            self._sort_order = 0 if self._sort_order else 1
        else:
            self._sort_col = col
            self._sort_order = 0
        order = Qt.DescendingOrder if self._sort_order else Qt.AscendingOrder
        self.table.horizontalHeader().setSortIndicator(col, order)
        self._ident_set(sort_col=self._sort_col, sort_order=self._sort_order)
        self._apply_sort()
        self._rebuild_table()

    def _sort_key(self, r: FileResult):
        """Sort key for the active sort column."""
        data = r.to_row()
        col = self._sort_col
        if col == self.C_FILE:
            return data["filename"].lower()
        if col == self.C_STATUS:
            return self._status_category(r)
        if col == self.C_EPISODE:
            g = r.guess
            return (g.season if g and g.season is not None else -1,
                    g.episode if g and g.episode is not None else -1)
        if col == self.C_TITLE:
            return (data.get("episode_title") or "").lower()
        if col == self.C_SUGGESTED:
            return (data.get("suggested_filename") or "").lower()
        if col == self.C_MATCH:
            return float(data.get("confidence") or 0.0)
        if col == self.C_AGREE:
            g = r.guess
            return (g.votes / g.total_samples) if g and g.total_samples else 0.0
        return data["filename"].lower()

    def _apply_sort(self):
        """Reorder ``self.results`` in place per the active sort state."""
        if not (0 <= self._sort_col < len(self.COLS)):
            return
        self.results.sort(key=self._sort_key,
                          reverse=bool(self._sort_order))

    def _rebuild_table(self):
        """Clear and re-render every row from ``self.results``.

        Used after a sort so the cell widgets (confidence pill, notes button)
        move with their data. The per-result checked state is preserved by
        result identity across the rebuild.
        """
        checked = {id(r) for r in self._checked_results()}
        self.table.setRowCount(0)
        for idx, r in enumerate(self.results):
            self._render_row(r, idx, checked=(id(r) in checked))
        self._update_status_counts()
        self._apply_filters()

    def _teardown_run(self):
        self.identify_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setVisible(False)
        self.progress_pct.setVisible(False)
        if self.state_tip:
            self.state_tip.setState(True)
            self.state_tip = None

    def _on_ok(self, total: int, review: int):
        self._teardown_run()
        # Apply the saved sort order now that every row is in.
        if 0 <= self._sort_col < len(self.COLS) and self.results:
            self._apply_sort()
            self._rebuild_table()
        ok = total - review
        InfoBar.success(
            "Done", f"{ok}/{total} identified confidently; {review} need review.",
            duration=6000, position=InfoBarPosition.TOP, parent=self)

    def _on_failed(self, msg: str):
        self._teardown_run()
        self._error("Identification failed", msg)

    def _on_cancelled(self, done: int):
        self._teardown_run()
        InfoBar.warning("Cancelled", f"Stopped after {done} file(s).",
                        duration=5000, position=InfoBarPosition.TOP, parent=self)

    # ----- exports / rename -----
    def _export_csv(self):
        if not self._has_results():
            return
        start = os.path.join(self.cfg.get("last_export_dir", HERE), "episode_map.csv")
        p, _ = QFileDialog.getSaveFileName(self, "Export CSV", start, "CSV (*.csv)")
        if p:
            write_csv(self.results, p)
            self.cfg.update(last_export_dir=os.path.dirname(p))
            self.cfg.save()
            InfoBar.success("Exported", f"CSV written to {p}", duration=4000,
                            position=InfoBarPosition.TOP, parent=self)

    def _export_json(self):
        if not self._has_results():
            return
        start = os.path.join(self.cfg.get("last_export_dir", HERE), "episode_map.json")
        p, _ = QFileDialog.getSaveFileName(self, "Export JSON", start, "JSON (*.json)")
        if p:
            write_json(self.results, p)
            self.cfg.update(last_export_dir=os.path.dirname(p))
            self.cfg.save()
            InfoBar.success("Exported", f"JSON written to {p}", duration=4000,
                            position=InfoBarPosition.TOP, parent=self)

    def _checked_results(self) -> List[FileResult]:
        picked: List[FileResult] = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                idx = chk.data(Qt.UserRole)
                if idx is not None and idx < len(self.results):
                    picked.append(self.results[idx])
        return picked

    def _on_dry_run_toggled(self, _state=None):
        """Persist the always-preview preference."""
        self._ident_set(dry_run_preview=bool(self.dry_run_check.isChecked()))

    def _collect_renamable(self) -> Optional[List[FileResult]]:
        """Return the checked results that have season/episode info, or None
        after surfacing the appropriate error."""
        if not self._has_results():
            return None
        picked = self._checked_results()
        if not picked:
            self._error("Nothing selected",
                        "Tick the checkbox next to the files you want to copy.")
            return None
        renamable = [r for r in picked if r.guess and r.guess.season is not None
                     and r.guess.episode is not None]
        if not renamable:
            self._error("No episode info",
                        "The selected files have no season/episode to rename by.")
            return None
        return renamable

    def _plan_for(self, r: FileResult, dest: str) -> dict:
        """Compute the source -> destination mapping for one result."""
        g = r.guess
        show = self._safe(g.title or "Show")
        season_dir = os.path.join(dest, show, f"Season {g.season:02d}")
        ext = os.path.splitext(r.path)[1]
        # Prefer the DB-correct name (includes the episode title) so Plex gets a
        # fully-titled file; fall back to a bare SxxEyy name if unknown.
        newname = r.suggested_filename or (
            f"{show} - {episode_id_str(g.season, g.episode)}{ext}")
        newname = self._safe(os.path.splitext(newname)[0]) + ext
        return {"src": r.path, "dest": os.path.join(season_dir, newname),
                "season_dir": season_dir, "filename": r.filename,
                "newname": newname}

    def _build_rename_plan(self, renamable: List[FileResult],
                           dest: str) -> List[dict]:
        """Build the full before -> after plan for a set of results."""
        return [self._plan_for(r, dest) for r in renamable]

    def _preview_renames(self):
        """Show the before/after plan without copying anything (dry run)."""
        renamable = self._collect_renamable()
        if renamable is None:
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Choose destination to preview renames",
            self.cfg.get("last_rename_dest", ""))
        if not dest:
            return
        plan = self._build_rename_plan(renamable, dest)
        dlg = RenamePreviewDialog(plan, dest, self.window())
        if dlg.exec():
            self._execute_rename_plan(plan, dest)

    def _rename_plex(self):
        """Copy checked files into a Plex layout, previewing first when the
        dry-run preference is on."""
        renamable = self._collect_renamable()
        if renamable is None:
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Choose destination for renamed copies",
            self.cfg.get("last_rename_dest", ""))
        if not dest:
            return
        plan = self._build_rename_plan(renamable, dest)
        if self.dry_run_check.isChecked():
            dlg = RenamePreviewDialog(plan, dest, self.window())
            if not dlg.exec():
                return
        self._execute_rename_plan(plan, dest)

    def _execute_rename_plan(self, plan: List[dict], dest: str):
        """Perform the copies described by ``plan`` and record them for undo."""
        self.cfg.update(last_rename_dest=dest)
        self.cfg.save()
        done, errors, ops = 0, [], []
        for item in plan:
            try:
                os.makedirs(item["season_dir"], exist_ok=True)
                shutil.copy2(item["src"], item["dest"])
                done += 1
                ops.append({"src": item["src"], "dest": item["dest"]})
                logging.info("copied -> %s", item["dest"])
            except Exception as exc:
                errors.append(f"{item['filename']}: {exc}")
        # Record the batch so it can be undone later.
        if ops:
            rename_history.record_batch(ops, destination=dest)
            self.undo_btn.setEnabled(True)
        if errors:
            self._error("Copied with errors",
                        f"Copied {done} file(s).\n" + "\n".join(errors[:6]))
        else:
            InfoBar.success("Renamed for Plex",
                            f"Copied {done} file(s) into a Plex layout under {dest}.",
                            duration=6000, position=InfoBarPosition.TOP, parent=self)

    def _undo_last_rename(self):
        """Reverse the most recent rename batch (deletes the copied files)."""
        batch = rename_history.peek_last_batch()
        if not batch:
            InfoBar.warning("Nothing to undo", "No rename batch on record.",
                            duration=4000, position=InfoBarPosition.TOP, parent=self)
            self.undo_btn.setEnabled(False)
            return
        ops = batch.get("operations", [])
        present = [o for o in ops if o.get("dest") and os.path.exists(o["dest"])]
        missing = len(ops) - len(present)
        when = batch.get("timestamp", "the last batch")
        sample = "\n".join(os.path.basename(o["dest"]) for o in present[:8])
        more = "" if len(present) <= 8 else f"\n...and {len(present) - 8} more"
        if not present:
            InfoBar.warning(
                "Nothing to remove",
                "None of the files from the last batch still exist.",
                duration=5000, position=InfoBarPosition.TOP, parent=self)
            rename_history.remove_last_batch()
            self.undo_btn.setEnabled(rename_history.peek_last_batch() is not None)
            return
        note = (f"\n\n{missing} file(s) are already gone and will be skipped."
                if missing else "")
        box = MessageBox(
            "Undo last rename",
            f"This will delete {len(present)} copied file(s) from the batch made "
            f"on {when}:\n\n{sample}{more}{note}\n\nThe original source files are "
            "not touched.",
            self.window())
        box.yesButton.setText("Delete copies")
        box.cancelButton.setText("Keep them")
        if not box.exec():
            return
        removed, errors = 0, []
        for o in present:
            try:
                os.remove(o["dest"])
                removed += 1
                # Tidy up now-empty Season / Show folders left behind.
                self._prune_empty_dirs(os.path.dirname(o["dest"]),
                                       o.get("dest", ""))
            except Exception as exc:
                errors.append(f"{os.path.basename(o['dest'])}: {exc}")
        rename_history.remove_last_batch()
        self.undo_btn.setEnabled(rename_history.peek_last_batch() is not None)
        if errors:
            self._error("Undo finished with errors",
                        f"Removed {removed} file(s).\n" + "\n".join(errors[:6]))
        else:
            InfoBar.success("Undo complete",
                            f"Removed {removed} copied file(s).",
                            duration=5000, position=InfoBarPosition.TOP, parent=self)

    @staticmethod
    def _prune_empty_dirs(start_dir: str, _dest: str):
        """Remove ``start_dir`` and its now-empty parent (Season then Show) if
        they are empty. Never raises and never climbs above two levels."""
        for _ in range(2):
            try:
                if os.path.isdir(start_dir) and not os.listdir(start_dir):
                    os.rmdir(start_dir)
                    start_dir = os.path.dirname(start_dir)
                else:
                    break
            except OSError:
                break

    @staticmethod
    def _safe(name: str) -> str:
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.strip().rstrip(".")

    def _has_results(self) -> bool:
        if not self.results:
            InfoBar.warning("No results", "Identify some files first.",
                            duration=4000, position=InfoBarPosition.TOP, parent=self)
            return False
        return True

    def _error(self, title: str, msg: str):
        InfoBar.error(title, msg, duration=7000,
                      position=InfoBarPosition.TOP, parent=self)


class RenamePreviewDialog(MessageBoxBase):
    """A dry-run dialog listing every planned before -> after copy.

    Purely informational: it copies nothing itself. It returns accepted
    (``exec()`` truthy) when the user chooses to proceed, so the caller can then
    perform the copies, or rejected when they cancel.
    """

    def __init__(self, plan: List[dict], dest: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(
            f"Preview: {len(plan)} file(s) to copy", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(
            f"Nothing is copied until you confirm. Destination root: {dest}",
            self))

        view = TextEdit(self)
        view.setReadOnly(True)
        view.setLineWrapMode(TextEdit.NoWrap)
        lines = []
        for item in plan:
            rel = os.path.relpath(item["dest"], dest)
            marker = "  (overwrites existing)" if os.path.exists(
                item["dest"]) else ""
            lines.append(f"{item['filename']}\n    ->  {rel}{marker}\n")
        view.setPlainText("\n".join(lines) if lines else "Nothing to copy.")
        # The list scrolls inside a modest fixed-minimum area; let it expand to
        # fill whatever height the dialog is given rather than forcing a tall
        # window that can overflow small screens.
        view.setMinimumHeight(160)
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.viewLayout.addWidget(view, 1)

        self.yesButton.setText("Proceed with copy")
        self.cancelButton.setText("Cancel")
        self.widget.setMinimumWidth(720)
        # Cap the dialog height to 80% of the available screen so the fixed
        # button row at the bottom is always reachable; the file list scrolls.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry().height()
            self.widget.setMaximumHeight(int(avail * 0.8))
        # Nothing to do if the plan is empty.
        self.yesButton.setEnabled(bool(plan))


class CustomizeViewDialog(MessageBoxBase):
    """Pick which optional panels are shown on the Identify page (Advanced).

    Presents one checkbox per toggleable panel. ``exec()`` is truthy when the
    user accepts; :meth:`selected_panels` then returns the chosen visibility
    mapping for storing back into config.
    """

    _PANELS = [
        ("options", "Options card (samples, workers, thresholds)"),
        ("legend", "Status colour legend"),
        ("dry_run", "Always-preview-renames row"),
        ("exports", "Export JSON / CSV buttons"),
    ]

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("Customize view", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(
            "Choose which optional panels appear on the Identify page.", self))

        self._checks: dict = {}
        for key, label in self._PANELS:
            cb = CheckBox(label, self)
            cb.setChecked(bool(current.get(key, True)))
            self.viewLayout.addWidget(cb)
            self._checks[key] = cb

        self.yesButton.setText("Apply")
        self.cancelButton.setText("Cancel")
        self.widget.setMinimumWidth(420)

    def selected_panels(self) -> dict:
        return {key: cb.isChecked() for key, cb in self._checks.items()}
