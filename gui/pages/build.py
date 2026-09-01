#!/usr/bin/env python3
"""Build library page: turn subtitle files into phonetic fingerprints."""
from __future__ import annotations

import os
import sys
from typing import List, Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFileDialog

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, LineEdit,
    IndeterminateProgressBar, TextEdit, InfoBar, InfoBarPosition,
    TitleLabel, CaptionLabel,
)

from audio_fingerprint.gui.constants import HERE, DEFAULT_DB
from audio_fingerprint.gui.widgets import Card, _path_row
from audio_fingerprint.gui.workers import BuildWorker


class BuildInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.cfg = window.gui_cfg
        self.setObjectName("buildInterface")
        self.worker: Optional[BuildWorker] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        root.addWidget(TitleLabel("Build reference library"))
        root.addWidget(CaptionLabel(
            "Dialogue (phonetic) matching needs subtitle files (.srt/.vtt) for "
            "the episodes you want to identify. Add a folder or a single file."))

        # subtitles card
        subs_card = Card("Add subtitles  (phonetic reference)")
        self.subs_edit = _path_row("Folder or .srt/.vtt file")
        self.subs_edit.setText(self.cfg.get("last_subtitle_source", ""))
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
        self.subs_btn = PrimaryPushButton("Add subtitles to library", self, FIF.FONT)
        self.subs_btn.clicked.connect(self._build_subs)
        subs_card.addWidget(self.subs_btn)
        root.addWidget(subs_card)

        self.build_progress = IndeterminateProgressBar()
        self.build_progress.setVisible(False)
        root.addWidget(self.build_progress)

        out_card = Card("Build output")
        self.build_out = TextEdit()
        self.build_out.setReadOnly(True)
        self.build_out.setMinimumHeight(160)
        out_card.addWidget(self.build_out)
        root.addWidget(out_card, 1)

    def _pick(self, edit: LineEdit, is_dir: bool, filt: str = "All files (*.*)"):
        if is_dir:
            p = QFileDialog.getExistingDirectory(self, "Select folder", edit.text().strip())
            if p:
                edit.setText(p)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
            if p:
                edit.setText(p)

    def _build_subs(self):
        path = self.subs_edit.text().strip()
        if not path or not os.path.exists(path):
            self._error("Pick a subtitle file or folder first.")
            return
        show_title = self.show_title_edit.text().strip()
        self.cfg.update(last_subtitle_source=path, last_show_title=show_title)
        self.cfg.save()
        flag = "--dir" if os.path.isdir(path) else "--file"
        # Build in parallel using the shared "Max parallel workers" setting
        # (Settings page). Folder builds fan out across worker threads; a single
        # file falls back to sequential inside the builder automatically.
        workers = int(self.cfg.get("max_workers", 4))
        # Run the builder as a module (BuildWorker runs with cwd=HERE, so the
        # cli package resolves). This is the new home of create_fingerprint.py.
        cmd = [sys.executable, "-m", "cli.build_fingerprints",
               flag, path, "--db", self.win.current_db_path() or DEFAULT_DB,
               "--workers", str(workers)]
        if show_title:
            cmd += ["--show-title", show_title]
        self._run(cmd)

    def _run(self, cmd: List[str]):
        if self.worker and self.worker.isRunning():
            self._error("A build task is already running.")
            return
        self.subs_btn.setEnabled(False)
        self.build_progress.setVisible(True)
        self.build_out.append(f"$ {' '.join(cmd)}")
        self.worker = BuildWorker(cmd)
        self.worker.output.connect(self.build_out.append)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, code: int):
        self.build_progress.setVisible(False)
        self.subs_btn.setEnabled(True)
        if code == 0:
            InfoBar.success("Library updated", "Fingerprints added to the database.",
                            duration=5000, position=InfoBarPosition.TOP, parent=self)
        else:
            InfoBar.error("Build failed",
                          f"The task exited with code {code}. See the output/Log.",
                          duration=6000, position=InfoBarPosition.TOP, parent=self)

    def _error(self, msg: str):
        InfoBar.error("Build library", msg, duration=5000,
                      position=InfoBarPosition.TOP, parent=self)
