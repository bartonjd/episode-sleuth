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
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidgetItem, QHeaderView, QFileDialog, QAbstractItemView,
)

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, SpinBox, DoubleSpinBox,
    TableWidget, ProgressBar, StateToolTip, InfoBar, InfoBarPosition,
    BodyLabel, TitleLabel, CaptionLabel,
)

from identify_dvd_episodes import (
    FileResult, write_csv, write_json, episode_id_str,
)

from audio_fingerprint.gui.constants import (
    HERE, COLOR_OK, COLOR_MEDIUM, COLOR_REVIEW,
)
from audio_fingerprint.gui.widgets import Card, _path_row
from audio_fingerprint.gui.workers import IdentifyWorker


class IdentifyInterface(QWidget):
    COLS = ["", "File", "Status", "Episode", "Episode Title",
            "Suggested Name", "Conf.", "Method", "Agree", "Notes"]

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

        # --- results table ---
        self.table = TableWidget()
        self.table.setColumnCount(len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)      # File
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)      # Episode Title
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)      # Suggested Name
        hdr.setSectionResizeMode(9, QHeaderView.Stretch)      # Notes
        for c in (2, 3, 6, 7, 8):  # Status, Episode, Conf, Method, Agree
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
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
        )

        self.table.setRowCount(0)
        self.results = []
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

    def _add_row(self, r: FileResult):
        self.results.append(r)
        row = self.table.rowCount()
        self.table.insertRow(row)
        data = r.to_row()
        conf = data["confidence"]
        status = data.get("name_status", "unknown")

        # Tint + auto-check driven by the naming verdict (the point of the tool):
        #   correct -> green, rename -> amber (action needed), unknown -> red.
        if r.needs_review or status == "unknown":
            tint = COLOR_REVIEW
            auto_check = False
            status_text = "Review"
        elif status == "correct":
            tint = COLOR_OK
            auto_check = True
            status_text = "Correct"
        else:  # rename
            tint = COLOR_MEDIUM
            auto_check = True
            status_text = "Rename"

        # checkbox column (stores the result index in UserRole)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if auto_check else Qt.Unchecked)
        chk.setData(Qt.UserRole, len(self.results) - 1)
        self.table.setItem(row, 0, chk)

        notes = data["notes"] or ("ok" if not r.needs_review else "review")
        # For correctly-named files the suggested name equals the current name;
        # show a dash to keep the column uncluttered. Otherwise show the target.
        suggested = data.get("suggested_filename", "")
        if status == "correct":
            suggested_disp = "-"
        else:
            suggested_disp = suggested or "-"
        values = [
            data["filename"],
            status_text,
            data["episode_id"],
            data.get("episode_title", ""),
            suggested_disp,
            f"{conf:.0%}",
            data["method"],
            data["agreement"],
            notes,
        ]
        for col, text in enumerate(values, start=1):
            item = QTableWidgetItem(str(text))
            if col in (2, 3, 6, 8):  # status, episode, conf, agree centered
                item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, item)

        brush = QBrush(tint)
        for col in range(self.table.columnCount()):
            it = self.table.item(row, col)
            if it is not None:
                it.setBackground(brush)

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
