#!/usr/bin/env python3
"""Identify page: match a folder of DVD rips to their episodes."""
from __future__ import annotations

import os
import shutil
import logging
from types import SimpleNamespace
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QTableWidgetItem, QHeaderView, QFileDialog, QAbstractItemView,
)

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, SpinBox, DoubleSpinBox,
    TableWidget, ProgressBar, StateToolTip, InfoBar, InfoBarPosition,
    BodyLabel, TitleLabel, CaptionLabel, MessageBox, SearchLineEdit, ToolButton,
)

from engine import (
    FileResult, write_csv, write_json, episode_id_str, titles_equivalent,
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

from ..constants import (
    HERE, COLOR_OK, COLOR_MEDIUM, COLOR_REVIEW,
    LEGEND_OK, LEGEND_MEDIUM, LEGEND_REVIEW, PILL_HIGH, PILL_MED, PILL_LOW,
)
from ..widgets import Card, _path_row
from ..workers import IdentifyWorker


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

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        root.addWidget(TitleLabel("Identify episodes"))
        root.addWidget(CaptionLabel(
            "Point at a folder of DVD rips and match each file to its episode. "
            "The reference database is set on the Settings page."))

        # --- source card ---
        src_card = Card("DVD rips to identify")
        self.source_edit = _path_row("Folder or single video/audio file")
        self.source_edit.setText(self.cfg.get("last_source", ""))
        folder_btn = PushButton("Folder", self, FIF.FOLDER)
        file_btn = PushButton("File", self, FIF.VIDEO)
        folder_btn.clicked.connect(self._pick_folder)
        file_btn.clicked.connect(self._pick_file)
        src_card.add(self.source_edit, folder_btn, file_btn)
        root.addWidget(src_card)

        # --- options card ---
        opt_card = Card("Options")
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

        self.rename_btn = PushButton("Rename for Plex", self, FIF.EDIT)
        self.export_json_btn = PushButton("Export JSON", self, FIF.SAVE_AS)
        self.export_csv_btn = PushButton("Export CSV", self, FIF.SAVE)
        self.rename_btn.clicked.connect(self._rename_plex)
        self.export_json_btn.clicked.connect(self._export_json)
        self.export_csv_btn.clicked.connect(self._export_csv)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.export_json_btn)
        actions.addWidget(self.export_csv_btn)
        root.addLayout(actions)

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

        # --- status colour legend ---
        root.addLayout(self._build_legend())

        # --- results table ---
        self.table = TableWidget()
        self.table.setColumnCount(len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
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
        root.addWidget(self.table, 1)

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

        self.table.setRowCount(0)
        self.results = []
        self._update_count()
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

    def _filter_results(self, text: str = ""):
        """Hide result rows that do not match the filter text (matched against
        the file name, episode id, episode title and suggested name)."""
        q = (text if text is not None else self.filter_edit.text()).strip().lower()
        for row in range(self.table.rowCount()):
            if not q:
                self.table.setRowHidden(row, False)
                continue
            hay = []
            for col in (self.C_FILE, self.C_EPISODE, self.C_TITLE, self.C_SUGGESTED):
                it = self.table.item(row, col)
                if it is not None:
                    hay.append(it.text().lower())
            self.table.setRowHidden(row, q not in " ".join(hay))
        self._update_count()

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
        self.results.append(r)
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
        chk.setCheckState(Qt.Checked if appear.auto_check else Qt.Unchecked)
        chk.setData(Qt.UserRole, len(self.results) - 1)
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
            title_item.setIcon(FIF.FLAG.icon())
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

        # Re-apply the current filter so newly added rows respect it.
        if self.filter_edit.text().strip():
            self._filter_results(self.filter_edit.text())
        else:
            self._update_count()

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

    def _rename_plex(self):
        if not self._has_results():
            return
        picked = self._checked_results()
        if not picked:
            self._error("Nothing selected",
                        "Tick the checkbox next to the files you want to copy.")
            return
        renamable = [r for r in picked if r.guess and r.guess.season is not None
                     and r.guess.episode is not None]
        if not renamable:
            self._error("No episode info",
                        "The selected files have no season/episode to rename by.")
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Choose destination for renamed copies",
            self.cfg.get("last_rename_dest", ""))
        if not dest:
            return
        self.cfg.update(last_rename_dest=dest)
        self.cfg.save()

        done, errors = 0, []
        for r in renamable:
            g = r.guess
            show = self._safe(g.title or "Show")
            season_dir = os.path.join(dest, show, f"Season {g.season:02d}")
            os.makedirs(season_dir, exist_ok=True)
            ext = os.path.splitext(r.path)[1]
            # Prefer the DB-correct name (includes the episode title) so Plex
            # gets a fully-titled file; fall back to bare SxxEyy if unknown.
            newname = r.suggested_filename or (
                f"{show} - {episode_id_str(g.season, g.episode)}{ext}")
            newname = self._safe(os.path.splitext(newname)[0]) + ext
            try:
                shutil.copy2(r.path, os.path.join(season_dir, newname))
                done += 1
                logging.info("copied -> %s", os.path.join(season_dir, newname))
            except Exception as exc:
                errors.append(f"{r.filename}: {exc}")
        if errors:
            self._error("Copied with errors",
                        f"Copied {done} file(s).\n" + "\n".join(errors[:6]))
        else:
            InfoBar.success("Renamed for Plex",
                            f"Copied {done} file(s) into a Plex layout under {dest}.",
                            duration=6000, position=InfoBarPosition.TOP, parent=self)

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
