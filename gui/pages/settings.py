#!/usr/bin/env python3
"""Settings page: database, engine config, appearance, performance and the
Vosk speech-to-text model manager."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog,
)

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, SpinBox, ComboBox,
    ProgressBar, InfoBar, InfoBarPosition, BodyLabel, TitleLabel, CaptionLabel,
    StrongBodyLabel,
)

from audio_fingerprint.gui.constants import DEFAULT_DB
from audio_fingerprint.gui.widgets import Card, _path_row
from audio_fingerprint.gui.workers import ModelDownloadWorker

# Speech-to-text helpers, imported defensively (the page degrades gracefully if
# the optional pydub/vosk dependencies are missing).
try:
    import stt_utils
except Exception:  # pragma: no cover - only if deps are absent
    stt_utils = None


class SettingsInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.cfg = window.gui_cfg
        self.setObjectName("settingsInterface")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        root.addWidget(TitleLabel("Settings"))
        root.addWidget(CaptionLabel(
            "These preferences are saved to gui_config.json, separate from the "
            "engine's config.json."))

        # database card
        db_card = Card("Reference fingerprint database")
        self.db_edit = _path_row("Path to fingerprints.db")
        self.db_edit.setText(self.cfg.get("db_path", "")
                             or (DEFAULT_DB if os.path.exists(DEFAULT_DB) else ""))
        db_browse = PushButton("Browse", self, FIF.FOLDER)
        db_browse.clicked.connect(self._pick_db)
        db_card.add(self.db_edit, db_browse)
        root.addWidget(db_card)

        # engine config card
        eng_card = Card("Engine configuration (optional)")
        self.eng_edit = _path_row("Path to config.json (blank = use default)")
        self.eng_edit.setText(self.cfg.get("engine_config_path", ""))
        eng_browse = PushButton("Browse", self, FIF.DOCUMENT)
        eng_browse.clicked.connect(self._pick_engine)
        eng_card.add(self.eng_edit, eng_browse)
        root.addWidget(eng_card)

        # appearance card
        appear_card = Card("Appearance")
        appear_grid = QGridLayout()
        appear_grid.setHorizontalSpacing(24)
        appear_grid.setVerticalSpacing(10)
        appear_grid.addWidget(BodyLabel("Theme"), 0, 0)
        self.theme_combo = ComboBox()
        self.theme_combo.addItems(["Dark", "Light", "Auto"])
        self.theme_combo.setCurrentText(self.cfg.get("theme", "Dark"))
        self.theme_combo.currentTextChanged.connect(self._change_theme)
        appear_grid.addWidget(self.theme_combo, 1, 0)
        appear_grid.setColumnStretch(1, 1)
        appear_card.addLayout(appear_grid)
        root.addWidget(appear_card)

        # performance card
        perf_card = Card("Performance")
        perf_grid = QGridLayout()
        perf_grid.setHorizontalSpacing(24)
        perf_grid.setVerticalSpacing(10)
        perf_grid.addWidget(BodyLabel("Max parallel workers"), 0, 0)
        self.workers_spin = SpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(int(self.cfg.get("max_workers", 4)))
        perf_grid.addWidget(self.workers_spin, 1, 0)
        perf_grid.addWidget(
            CaptionLabel("Number of files identified at the same time. Higher "
                         "values are faster on multi-core machines."), 2, 0)
        perf_grid.setColumnStretch(1, 1)
        perf_card.addLayout(perf_grid)
        root.addWidget(perf_card)

        # speech recognition card
        stt_card = Card("Speech recognition (Vosk model)")
        stt_grid = QGridLayout()
        stt_grid.setHorizontalSpacing(16)
        stt_grid.setVerticalSpacing(10)

        stt_grid.addWidget(BodyLabel("Model size"), 0, 0)
        self.model_combo = ComboBox()
        self._model_labels = ["Small (39 MB)", "Large (1.8 GB)"]
        self._model_values = ["small", "large"]
        self.model_combo.addItems(self._model_labels)
        cur_size = self.cfg.get("vosk_model_size", "small")
        idx = self._model_values.index(cur_size) if cur_size in self._model_values else 0
        self.model_combo.setCurrentIndex(idx)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        stt_grid.addWidget(self.model_combo, 0, 1)

        # Download / update button + live status live to the right of the combo.
        self.model_download_btn = PushButton("Download", self, FIF.DOWNLOAD)
        self.model_download_btn.clicked.connect(self._start_model_download)
        stt_grid.addWidget(self.model_download_btn, 0, 2)

        self.model_status = CaptionLabel("")
        stt_grid.addWidget(self.model_status, 1, 1, 1, 2)

        # Progress bar + percentage caption, hidden until a download runs.
        # Spans the full width of the card and is made taller so a running
        # download is clearly visible next to the Download button.
        self.model_progress = ProgressBar()
        self.model_progress.setFixedHeight(10)
        self.model_progress.setVisible(False)
        stt_grid.addWidget(self.model_progress, 2, 0, 1, 3)
        # A large, prominent percentage / MB readout (StrongBodyLabel is bolder
        # and bigger than the CaptionLabel used elsewhere).
        self.model_progress_label = StrongBodyLabel("")
        self.model_progress_label.setVisible(False)
        stt_grid.addWidget(self.model_progress_label, 3, 0, 1, 3)

        stt_grid.addWidget(
            CaptionLabel("The large model is far more accurate on clean DVD-rip "
                         "audio but uses ~1.8 GB. Choose a size, then click "
                         "Download. Re-downloading refreshes it to the latest "
                         "published build."), 4, 0, 1, 3)
        stt_grid.setColumnStretch(1, 1)
        stt_card.addLayout(stt_grid)
        root.addWidget(stt_card)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = PrimaryPushButton("Save settings", self, FIF.SAVE)
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        root.addLayout(save_row)
        root.addStretch(1)

        # Track the model size that is actually saved, plus any live download.
        self._saved_model_size = cur_size
        self._dl_worker = None
        self._refresh_model_ui()

    def _pick_db(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select fingerprint database", self.db_edit.text().strip(),
            "SQLite DB (*.db);;All files (*.*)")
        if p:
            self.db_edit.setText(p)

    def _pick_engine(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select engine config.json", self.eng_edit.text().strip(),
            "JSON (*.json);;All files (*.*)")
        if p:
            self.eng_edit.setText(p)

    def _change_theme(self, name: str):
        self.win.apply_theme(name)

    # ---- Vosk model management -------------------------------------------
    def _selected_model_size(self) -> str:
        return self._model_values[self.model_combo.currentIndex()]

    def _model_present(self, size: str) -> bool:
        """True if the model for ``size`` is already downloaded on disk."""
        if stt_utils is None:
            return False
        try:
            return stt_utils.get_model_path(size) is not None
        except Exception:
            return False

    def _downloading(self) -> bool:
        return self._dl_worker is not None and self._dl_worker.isRunning()

    def _refresh_model_ui(self):
        """Single source of truth for the model card + Save button state.

        Rules:
          * While a download runs, lock the combo/download button and Save.
          * If the selected model is not on disk, Save is disabled and a hint
            tells the user to download it first.
          * If it is present, Save is enabled and the button offers to
            re-download / update to the latest build.
        """
        if self._downloading():
            self.model_combo.setEnabled(False)
            self.model_download_btn.setEnabled(True)  # doubles as Cancel
            self.model_download_btn.setText("Cancel")
            self.save_btn.setEnabled(False)
            self.model_status.setText("Downloading model...")
            return

        # Not downloading: normal state.
        self.model_combo.setEnabled(True)
        self.model_progress.setVisible(False)
        self.model_progress_label.setVisible(False)
        size = self._selected_model_size()
        present = self._model_present(size)

        if stt_utils is None:
            self.model_download_btn.setEnabled(False)
            self.model_download_btn.setText("Download")
            self.model_status.setText(
                "Speech-to-text module unavailable - cannot manage models.")
            # Do not block saving other settings in this degraded state.
            self.save_btn.setEnabled(True)
            return

        self.model_download_btn.setEnabled(True)
        if present:
            self.model_download_btn.setText("Re-download / Update")
            self.model_status.setText("Installed - ready to use.")
            self.save_btn.setEnabled(True)
        else:
            self.model_download_btn.setText("Download")
            self.model_status.setText(
                "Not downloaded. Click Download before saving this model choice.")
            self.save_btn.setEnabled(False)

    def _on_model_changed(self, _idx: int):
        # Changing the selector never triggers a download by itself; it just
        # updates the state (and disables Save if the new choice is missing).
        self._refresh_model_ui()

    def _start_model_download(self):
        # The button doubles as a Cancel control while a download is running.
        if self._downloading():
            self._dl_worker.cancel()
            self.model_download_btn.setEnabled(False)
            self.model_status.setText("Cancelling...")
            return

        if stt_utils is None:
            InfoBar.error(
                "Unavailable",
                "The speech-to-text module could not be loaded, so models "
                "cannot be downloaded.",
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            return

        size = self._selected_model_size()
        present = self._model_present(size)
        # Present -> user asked to update, so force a fresh pull of the latest
        # published build; missing -> a normal first download.
        self._dl_worker = ModelDownloadWorker(size, force=present)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finishedOk.connect(self._on_dl_ok)
        self._dl_worker.failed.connect(self._on_dl_failed)
        self._dl_worker.finished.connect(self._on_dl_thread_done)

        self.model_progress.setVisible(True)
        self.model_progress.setValue(0)
        self.model_progress_label.setVisible(True)
        self.model_progress_label.setText("Starting download...")

        # Let the user know the download is running and set expectations for the
        # large model, which can take several minutes.
        if size == "large":
            msg = ("Downloading the large Vosk model (~1.8 GB). This may take "
                   "several minutes - progress is shown below the button.")
        else:
            msg = ("Downloading the small Vosk model (~39 MB). Progress is "
                   "shown below the button.")
        InfoBar.info(
            "Downloading Vosk model", msg,
            duration=6000, position=InfoBarPosition.TOP, parent=self)

        self._refresh_model_ui()
        self._dl_worker.start()

    def _on_dl_progress(self, done: int, total: int):
        mb = 1024 * 1024
        if total > 0:
            pct = int(done * 100 / total)
            self.model_progress.setValue(pct)
            self.model_progress_label.setText(
                f"{pct}%  ({done / mb:.1f} MB of {total / mb:.1f} MB)")
        else:
            # Unknown length: show indeterminate-style text but keep the bar.
            self.model_progress.setValue(0)
            self.model_progress_label.setText(f"{done / mb:.1f} MB downloaded...")

    def _on_dl_ok(self, path: str):
        self.model_progress.setValue(100)
        InfoBar.success(
            "Model ready",
            "The Vosk model was downloaded successfully.",
            duration=5000, position=InfoBarPosition.TOP, parent=self)

    def _on_dl_failed(self, msg: str):
        InfoBar.error(
            "Download failed", msg,
            duration=8000, position=InfoBarPosition.TOP, parent=self)

    def _on_dl_thread_done(self):
        # Runs whether the download succeeded, failed, or was cancelled.
        self._dl_worker = None
        self._refresh_model_ui()

    def _save(self):
        # Guard: never save a model choice whose files are not on disk, or while
        # a download is in flight (belt-and-braces - the button is disabled too).
        size = self._selected_model_size()
        if self._downloading():
            InfoBar.warning(
                "Please wait",
                "A model download is in progress. Let it finish before saving.",
                duration=5000, position=InfoBarPosition.TOP, parent=self)
            return
        if stt_utils is not None and not self._model_present(size):
            InfoBar.warning(
                "Download required",
                "Download the selected Vosk model before saving this choice.",
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            self._refresh_model_ui()
            return

        self.cfg.update(
            db_path=self.db_edit.text().strip(),
            engine_config_path=self.eng_edit.text().strip(),
            theme=self.theme_combo.currentText(),
            max_workers=int(self.workers_spin.value()),
            vosk_model_size=size,
        )
        self.cfg.save()
        self._saved_model_size = size
        InfoBar.success("Saved", "Your settings have been saved.",
                        duration=4000, position=InfoBarPosition.TOP, parent=self)
