#!/usr/bin/env python3
"""Build library page: turn subtitle files into phonetic fingerprints."""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..main_window import MainWindow

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TextEdit,
    TitleLabel,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..constants import DEFAULT_DB
from ..path_utils import native_path
from ..widgets import Card, _path_row
from ..workers import LibraryBuildWorker
from ..workers import _resolve_primary_language as _resolve_build_language


class BuildInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.cfg = window.gui_cfg
        self.setObjectName("buildInterface")
        self.worker: Optional[LibraryBuildWorker] = None

        # Elapsed-time tracking for the running build. A 1 Hz timer keeps the
        # status label ("... 00:42 elapsed") ticking while a build runs.
        self._start_time: float = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._last_progress = (0, 0, "")

        # --- page content (inside a scroll area so nothing squishes/overlaps on
        # short windows, matching the Identify page) -------------------------
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        root.addWidget(TitleLabel("Build reference library"))
        root.addWidget(CaptionLabel(
            "Dialogue (phonetic) matching needs subtitle files (.srt/.vtt) for "
            "the episodes you want to identify. Add a folder or a single file."))

        # subtitles card
        subs_card = Card("Add subtitles  (phonetic reference)")
        self.subs_edit = _path_row("Folder or .srt/.vtt file")
        self.subs_edit.setText(native_path(self.cfg.get("last_subtitle_source", "")))
        subs_folder = PushButton("Folder", self, FIF.FOLDER)
        subs_file = PushButton("File", self, FIF.DOCUMENT)
        subs_folder.clicked.connect(lambda: self._pick(self.subs_edit, True))
        subs_file.clicked.connect(lambda: self._pick(
            self.subs_edit, False, "Subtitles (*.srt *.vtt);;All files (*.*)"))
        subs_card.add(self.subs_edit, subs_folder, subs_file)
        subs_card.addWidget(CaptionLabel(
            "TV show title (optional) - applied to every subtitle in this batch. "
            "Improves matching accuracy by anchoring results to the show."))
        self.show_title_edit = LineEdit()
        self.show_title_edit.setPlaceholderText("e.g. Matlock")
        self.show_title_edit.setText(self.cfg.get("last_show_title", ""))
        self.show_title_edit.setClearButtonEnabled(True)
        subs_card.addWidget(self.show_title_edit)

        # Overwrite existing entries: when checked, re-process files already in
        # the database and replace their fingerprints instead of skipping them.
        self.overwrite_check = CheckBox(
            "Overwrite existing entries (re-index files already in the library)")
        self.overwrite_check.setChecked(
            bool(self.cfg.get("build_overwrite", False)))
        subs_card.addWidget(self.overwrite_check)

        # Fetch episode durations online (TVMaze). Stored as the expected runtime
        # and used to flag duration mismatches at identify time.
        self.duration_check = CheckBox(
            "Fetch episode durations online (TVMaze) for runtime validation")
        self.duration_check.setChecked(
            bool(self.cfg.get("build_fetch_duration", False)))
        subs_card.addWidget(self.duration_check)

        self.subs_btn = PrimaryPushButton("Add subtitles to library", self, FIF.FONT)
        self.subs_btn.clicked.connect(self._build_subs)
        subs_card.addWidget(self.subs_btn)
        root.addWidget(subs_card)

        # Progress: an activity bar plus a live status line showing which file is
        # being processed and how long the build has been running.
        self.build_progress = IndeterminateProgressBar()
        self.build_progress.setVisible(False)
        root.addWidget(self.build_progress)

        self.status_label = BodyLabel("")
        self.status_label.setVisible(False)
        root.addWidget(self.status_label)

        out_card = Card("Build output")
        self.build_out = TextEdit()
        self.build_out.setReadOnly(True)
        self.build_out.setMinimumHeight(160)
        out_card.addWidget(self.build_out)
        root.addWidget(out_card, 1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.addWidget(scroll)

    def _pick(self, edit: LineEdit, is_dir: bool, filt: str = "All files (*.*)"):
        if is_dir:
            p = QFileDialog.getExistingDirectory(self, "Select folder", edit.text().strip())
            if p:
                edit.setText(native_path(p))
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
            if p:
                edit.setText(native_path(p))

    def _build_subs(self):
        if self.worker and self.worker.isRunning():
            self._error("A build task is already running.")
            return
        path = self.subs_edit.text().strip()
        if not path or not os.path.exists(path):
            self._error("Pick a subtitle file or folder first.")
            return
        show_title = self.show_title_edit.text().strip()
        overwrite = self.overwrite_check.isChecked()
        fetch_duration = self.duration_check.isChecked()
        self.cfg.update(last_subtitle_source=path, last_show_title=show_title,
                        build_overwrite=overwrite,
                        build_fetch_duration=fetch_duration)
        self.cfg.save()

        # Build in parallel using the shared "Max parallel workers" setting
        # (Settings page). Folder builds fan out across worker threads; a single
        # file falls back to sequential inside the builder automatically.
        workers = int(self.cfg.get("max_workers", 4))
        # Honour the Primary Language chosen in Settings. "Auto-detect" (or an
        # unset value) resolves to None so the builder detects each file's
        # language individually; any specific choice is passed through so the
        # library is encoded consistently for that language.
        lang = _resolve_build_language(self.cfg.get("primary_language"))
        db_path = self.win.current_db_path() or DEFAULT_DB
        config_path = self.win.current_engine_config() or None

        # Run the builder IN-PROCESS on a worker thread. This replaced an older
        # implementation that shelled out to "sys.executable -m
        # cli.build_fingerprints"; under a frozen (PyInstaller) build that
        # launched a second copy of the whole GUI (the dual-window bug).
        self.subs_btn.setEnabled(False)
        self.build_progress.setVisible(True)
        self.status_label.setVisible(True)
        self._last_progress = (0, 0, "")
        self.status_label.setText("Starting build... 00:00 elapsed")
        src = "folder" if os.path.isdir(path) else "file"
        self.build_out.append(f"$ Building library from {src}: {path}")
        if lang:
            self.build_out.append(f"  language: {lang}")
        self.build_out.append(
            f"  database: {db_path}  |  workers: {workers}"
            f"{'  |  overwrite' if overwrite else ''}"
            f"{'  |  fetch durations' if fetch_duration else ''}")

        self._start_time = time.monotonic()
        self._elapsed_timer.start()

        self.worker = LibraryBuildWorker(
            path=path, db_path=db_path, config_path=config_path,
            show_title=show_title, overwrite=overwrite,
            fetch_duration=fetch_duration, workers=workers, language=lang)
        self.worker.output.connect(self.build_out.append)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _format_elapsed(self) -> str:
        secs = int(max(0.0, time.monotonic() - self._start_time))
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _tick_elapsed(self):
        done, total, name = self._last_progress
        self.status_label.setText(self._compose_status(done, total, name))

    def _compose_status(self, done: int, total: int, name: str) -> str:
        elapsed = self._format_elapsed()
        if total > 0:
            head = f"Processing file {done} of {total}"
            if name:
                head += f": {name}"
            return f"{head}  -  {elapsed} elapsed"
        return f"Working...  -  {elapsed} elapsed"

    def _on_progress(self, done: int, total: int, name: str):
        self._last_progress = (done, total, name)
        self.status_label.setText(self._compose_status(done, total, name))

    def _on_done(self, code: int, total: int, processed: int, skipped: int):
        self._elapsed_timer.stop()
        elapsed = self._format_elapsed()
        self.build_progress.setVisible(False)
        self.subs_btn.setEnabled(True)
        if code == 0:
            self.status_label.setText(
                f"Done in {elapsed}  -  {total} fingerprints from "
                f"{processed} file(s), {skipped} skipped.")
            InfoBar.success(
                "Library updated",
                f"Added {total} fingerprints from {processed} file(s) "
                f"in {elapsed}.",
                duration=5000, position=InfoBarPosition.TOP, parent=self)
        else:
            self.status_label.setText(f"Build failed after {elapsed}.")
            InfoBar.error("Build failed",
                          "The build task failed. See the output/Log for details.",
                          duration=6000, position=InfoBarPosition.TOP, parent=self)

    def _error(self, msg: str):
        InfoBar.error("Build library", msg, duration=5000,
                      position=InfoBarPosition.TOP, parent=self)
