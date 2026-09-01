#!/usr/bin/env python3
"""Settings page: database, engine config, appearance, performance and the
Vosk speech-to-text model manager."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog,
)

from qfluentwidgets import (
    FluentIcon as FIF, PrimaryPushButton, PushButton, SpinBox, ComboBox,
    ProgressBar, InfoBar, InfoBarPosition, BodyLabel, TitleLabel, CaptionLabel,
    StrongBodyLabel, MessageBox,
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

# Core database helpers, imported defensively so the Settings page still loads
# even if the heavy engine dependencies are unavailable.
try:
    from fingerprint_core import FingerprintDB, validate_db_path
except Exception:  # pragma: no cover - only if deps are absent
    FingerprintDB = None
    validate_db_path = None

# Vosk model specs (used to show the exact model directory name in the download
# progress text, e.g. "Downloading vosk-model-small-en-us-0.15... 45%").
try:
    from constants import VOSK_MODELS
except Exception:  # pragma: no cover - only if constants is unavailable
    VOSK_MODELS = {
        "small": {"dir": "vosk-model-small-en-us-0.15"},
        "large": {"dir": "vosk-model-en-us-0.22"},
    }


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
        db_new = PushButton("Create New Database", self, FIF.ADD)
        db_new.clicked.connect(self._create_db)
        db_card.add(self.db_edit, db_browse, db_new)
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

        # Download / update button + a dedicated Cancel Download button that is
        # only shown while a download is running.
        self.model_download_btn = PushButton("Download", self, FIF.DOWNLOAD)
        self.model_download_btn.clicked.connect(self._start_model_download)
        stt_grid.addWidget(self.model_download_btn, 0, 2)

        self.model_cancel_btn = PushButton("Cancel Download", self, FIF.CANCEL)
        self.model_cancel_btn.clicked.connect(self._cancel_model_download)
        self.model_cancel_btn.setVisible(False)
        stt_grid.addWidget(self.model_cancel_btn, 0, 3)

        self.model_status = CaptionLabel("")
        stt_grid.addWidget(self.model_status, 1, 1, 1, 3)

        # Persistent progress bar + percentage caption, styled to match the
        # Identify tab's progress bar (thin, 6 px). Hidden until a download runs
        # and kept visible for the whole download - no disappearing InfoBar.
        self.model_progress = ProgressBar()
        self.model_progress.setFixedHeight(6)
        self.model_progress.setVisible(False)
        stt_grid.addWidget(self.model_progress, 2, 0, 1, 4)
        # A prominent status readout, e.g.
        # "Downloading vosk-model-small-en-us-0.15... 45%".
        self.model_progress_label = StrongBodyLabel("")
        self.model_progress_label.setVisible(False)
        stt_grid.addWidget(self.model_progress_label, 3, 0, 1, 4)

        stt_grid.addWidget(
            CaptionLabel("The large model is far more accurate on clean DVD-rip "
                         "audio but uses ~1.8 GB. Choose a size, then click "
                         "Download. Re-downloading refreshes it to the latest "
                         "published build."), 4, 0, 1, 4)
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

        # --- unsaved-changes tracking ------------------------------------------
        # Snapshot the values as they were loaded; anything that differs from
        # this snapshot counts as an unsaved change and triggers the Save /
        # Discard / Cancel prompt when the user leaves the Settings tab.
        self._original = self._snapshot()
        # React to edits so we could surface dirty state if needed later.
        self.db_edit.textChanged.connect(self._on_field_changed)
        self.eng_edit.textChanged.connect(self._on_field_changed)
        self.theme_combo.currentTextChanged.connect(self._on_field_changed)
        self.workers_spin.valueChanged.connect(self._on_field_changed)
        self.model_combo.currentIndexChanged.connect(self._on_field_changed)

    # ---- unsaved-changes helpers -----------------------------------------
    def _snapshot(self) -> dict:
        """Capture the current values of every persisted setting."""
        return {
            "db_path": self.db_edit.text().strip(),
            "engine_config_path": self.eng_edit.text().strip(),
            "theme": self.theme_combo.currentText(),
            "max_workers": int(self.workers_spin.value()),
            "vosk_model_size": self._selected_model_size(),
        }

    def _on_field_changed(self, *args):
        # Hook kept intentionally light; dirty state is computed on demand in
        # has_unsaved_changes() so it always reflects the live widgets.
        pass

    def has_unsaved_changes(self) -> bool:
        return self._snapshot() != getattr(self, "_original", None)

    def confirm_leave(self) -> bool:
        """Decide whether the user may leave the Settings tab.

        Returns True to allow leaving, False to stay. Handles two cases:
          * A model download in progress -> offer to cancel it and leave.
          * Unsaved changes -> Save / Discard / Cancel prompt.
        """
        # 1. A live download blocks a clean leave. Offer to cancel it.
        if self._downloading():
            box = MessageBox(
                "Download in progress",
                "A Vosk model download is still running. Leaving this tab will "
                "cancel it. Cancel the download and leave?",
                self.win)
            box.yesButton.setText("Cancel download and leave")
            box.cancelButton.setText("Stay")
            if not box.exec():
                return False
            # Cancel the download and wait briefly for the thread to unwind so
            # we never leave a live QThread running behind the scenes.
            if self._dl_worker is not None:
                self._dl_worker.cancel()
                self._dl_worker.wait(5000)
            # Fall through to the unsaved-changes check below.

        # 2. Unsaved changes -> Save / Discard / Cancel (three real buttons).
        if not self.has_unsaved_changes():
            return True

        choice = self._prompt_unsaved()
        if choice == "save":
            # Save. If the save is blocked (e.g. model not downloaded), stay.
            return self._save(from_leave=True)
        if choice == "discard":
            self._revert()
            return True
        # "cancel" -> stay on the Settings tab.
        return False

    def _prompt_unsaved(self) -> str:
        """Show a Save / Discard / Cancel dialog. Returns the chosen action.

        qfluentwidgets' MessageBox ships with two buttons (yes/cancel); a third
        real button is inserted into its button layout so the user gets the
        conventional three-way choice in a single, consistent Fluent dialog.
        """
        box = MessageBox(
            "Unsaved changes",
            "You have unsaved changes. Do you want to save them?",
            self.win)
        box.yesButton.setText("Save")
        box.cancelButton.setText("Cancel")

        # Insert a dedicated "Discard" button between Save and Cancel.
        discard_btn = PushButton("Discard", box.buttonGroup)
        box.buttonLayout.insertWidget(1, discard_btn, 1, Qt.AlignVCenter)

        self._unsaved_choice = "cancel"

        def _choose_discard():
            self._unsaved_choice = "discard"
            box.reject()

        discard_btn.clicked.connect(_choose_discard)

        # yesButton -> save (box returns True); cancelButton -> cancel/stay.
        if box.exec():
            return "save"
        return self._unsaved_choice

    def _revert(self):
        """Restore every widget to the last-loaded (original) values."""
        orig = getattr(self, "_original", None)
        if not orig:
            return
        self.db_edit.setText(orig.get("db_path", ""))
        self.eng_edit.setText(orig.get("engine_config_path", ""))
        self.theme_combo.setCurrentText(orig.get("theme", "Dark"))
        self.workers_spin.setValue(int(orig.get("max_workers", 4)))
        size = orig.get("vosk_model_size", "small")
        if size in self._model_values:
            self.model_combo.setCurrentIndex(self._model_values.index(size))
        # Re-apply the theme in case the live preview changed it.
        self.win.apply_theme(orig.get("theme", "Dark"))
        self._refresh_model_ui()

    def _pick_db(self):
        # Save mode lets the user either pick an existing database or type a new
        # filename that does not yet exist, instead of the "File not found"
        # dead-end an open dialog produces.
        dlg = QFileDialog(self, "Select or name a fingerprint database")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setFileMode(QFileDialog.AnyFile)
        dlg.setOption(QFileDialog.DontConfirmOverwrite, True)
        dlg.setNameFilter("SQLite Database (*.db);;All files (*.*)")
        dlg.setDefaultSuffix("db")
        start = self.db_edit.text().strip()
        if start:
            dlg.selectFile(start)
        if not dlg.exec():
            return
        chosen = dlg.selectedFiles()
        if not chosen:
            return
        p = chosen[0]
        self.db_edit.setText(p)
        # If the chosen path does not exist yet, offer to create it right away so
        # later operations (Build Library / Identify) do not fail.
        if not os.path.exists(p):
            self._offer_create_db(p)

    def _create_db(self):
        # A dedicated "Create New Database" flow: prompt for a filename, then
        # create a fresh, empty database with the full schema.
        dlg = QFileDialog(self, "Create new fingerprint database")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setFileMode(QFileDialog.AnyFile)
        dlg.setNameFilter("SQLite Database (*.db);;All files (*.*)")
        dlg.setDefaultSuffix("db")
        start = self.db_edit.text().strip()
        if start:
            dlg.selectFile(start)
        if not dlg.exec():
            return
        chosen = dlg.selectedFiles()
        if not chosen:
            return
        p = chosen[0]
        if os.path.exists(p):
            box = MessageBox(
                "Database already exists",
                f"A file already exists at:\n{p}\n\nUse this existing database?",
                self.win)
            box.yesButton.setText("Use it")
            box.cancelButton.setText("Cancel")
            if box.exec():
                self.db_edit.setText(p)
            return
        if self._init_new_db(p):
            self.db_edit.setText(p)

    def _offer_create_db(self, path: str) -> bool:
        """Ask whether to create a missing database, and create it if confirmed.

        Returns True if a database now exists at ``path`` (created or already
        present), False otherwise.
        """
        box = MessageBox(
            "Database does not exist",
            f"No database was found at:\n{path}\n\nCreate it now?",
            self.win)
        box.yesButton.setText("Create it")
        box.cancelButton.setText("Not now")
        if not box.exec():
            return False
        return self._init_new_db(path)

    def _init_new_db(self, path: str) -> bool:
        """Create parent directories and initialise an empty database + schema.

        Uses the engine's :class:`FingerprintDB`, which validates the path,
        auto-creates the parent directory and applies the full table schema.
        Returns True on success.
        """
        if FingerprintDB is None:
            InfoBar.error(
                "Unavailable",
                "The database engine could not be loaded, so a new database "
                "cannot be created.",
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            return False
        try:
            if validate_db_path is not None:
                validate_db_path(path)
            db = FingerprintDB(path)
            db.conn.close()
        except Exception as exc:
            InfoBar.error(
                "Could not create database", str(exc),
                duration=8000, position=InfoBarPosition.TOP, parent=self)
            return False
        InfoBar.success(
            "Database ready",
            f"An empty fingerprint database was created at {path}.",
            duration=5000, position=InfoBarPosition.TOP, parent=self)
        return True

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

    def _model_dirname(self, size: str) -> str:
        """The on-disk directory name for a model size (used in status text)."""
        spec = VOSK_MODELS.get(size) or VOSK_MODELS.get("small", {})
        return spec.get("dir", f"vosk-model-{size}")

    def _refresh_model_ui(self):
        """Single source of truth for the model card + Save button state.

        Rules:
          * While a download runs, lock the combo + Download button, show the
            dedicated Cancel Download button, and disable Save.
          * If the selected model is not on disk, Save is disabled and a hint
            tells the user to download it first.
          * If it is present, Save is enabled and the button offers to
            re-download / update to the latest build.
        """
        if self._downloading():
            self.model_combo.setEnabled(False)
            self.model_download_btn.setEnabled(False)
            self.model_cancel_btn.setVisible(True)
            self.model_cancel_btn.setEnabled(True)
            self.save_btn.setEnabled(False)
            self.model_status.setText("Downloading model...")
            return

        # Not downloading: normal state.
        self.model_combo.setEnabled(True)
        self.model_cancel_btn.setVisible(False)
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

    def _cancel_model_download(self):
        """Cancel an in-flight model download (dedicated Cancel Download button)."""
        if not self._downloading():
            return
        self._dl_worker.cancel()
        self.model_cancel_btn.setEnabled(False)
        self.model_status.setText("Cancelling...")
        self.model_progress_label.setText("Cancelling download...")

    def _start_model_download(self):
        # Guard against a double-start; the dedicated Cancel button handles
        # cancellation, so the Download button never doubles as one.
        if self._downloading():
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

        # Remember the model being fetched so progress text can name it, e.g.
        # "Downloading vosk-model-small-en-us-0.15... 45%".
        self._dl_model_name = self._model_dirname(size)

        self.model_progress.setVisible(True)
        self.model_progress.setRange(0, 100)
        self.model_progress.setValue(0)
        self.model_progress_label.setVisible(True)
        self.model_progress_label.setText(
            f"Downloading {self._dl_model_name}... 0%")

        # No disappearing InfoBar - the persistent progress bar + label below the
        # controls is the single, always-visible source of download status.
        self._refresh_model_ui()
        self._dl_worker.start()

    def _on_dl_progress(self, done: int, total: int):
        mb = 1024 * 1024
        name = getattr(self, "_dl_model_name", "Vosk model")
        if total > 0:
            pct = int(done * 100 / total)
            self.model_progress.setValue(pct)
            self.model_progress_label.setText(
                f"Downloading {name}... {pct}%  "
                f"({done / mb:.1f} MB of {total / mb:.1f} MB)")
        else:
            # Unknown length: show downloaded MB but keep the bar visible.
            self.model_progress.setValue(0)
            self.model_progress_label.setText(
                f"Downloading {name}... {done / mb:.1f} MB")

    def _on_dl_ok(self, path: str):
        name = getattr(self, "_dl_model_name", "Vosk model")
        self.model_progress.setValue(100)
        # Persistent success message in the status label (no InfoBar).
        self.model_status.setText(f"{name} installed - ready to use.")

    def _on_dl_failed(self, msg: str):
        # Show the failure persistently in the label instead of a fading InfoBar.
        self.model_progress_label.setText(f"Download failed: {msg}")

    def _on_dl_thread_done(self):
        # Runs whether the download succeeded, failed, or was cancelled.
        cancelled = self._dl_worker is not None and getattr(
            self._dl_worker, "_cancel", False)
        self._dl_worker = None
        self._refresh_model_ui()
        if cancelled:
            self.model_progress_label.setVisible(True)
            self.model_progress_label.setText("Download cancelled.")

    def _save(self, from_leave: bool = False) -> bool:
        """Persist settings. Returns True on success, False if blocked.

        ``from_leave`` is True when called from the unsaved-changes prompt while
        the user is leaving the tab; on a blocked save we then keep them on the
        Settings tab (return False) so nothing is silently lost.
        """
        # Guard: never save a model choice whose files are not on disk, or while
        # a download is in flight (belt-and-braces - the button is disabled too).
        size = self._selected_model_size()
        if self._downloading():
            InfoBar.warning(
                "Please wait",
                "A model download is in progress. Let it finish before saving.",
                duration=5000, position=InfoBarPosition.TOP, parent=self)
            return False
        if stt_utils is not None and not self._model_present(size):
            InfoBar.warning(
                "Download required",
                "Download the selected Vosk model before saving this choice.",
                duration=6000, position=InfoBarPosition.TOP, parent=self)
            self._refresh_model_ui()
            return False

        # If a database path is set but the file does not exist yet, offer to
        # create it now so Build Library / Identify do not fail later.
        db_path = self.db_edit.text().strip()
        if db_path and not os.path.exists(db_path):
            self._offer_create_db(db_path)

        self.cfg.update(
            db_path=self.db_edit.text().strip(),
            engine_config_path=self.eng_edit.text().strip(),
            theme=self.theme_combo.currentText(),
            max_workers=int(self.workers_spin.value()),
            vosk_model_size=size,
        )
        self.cfg.save()
        self._saved_model_size = size
        # The saved state becomes the new baseline for unsaved-change detection.
        self._original = self._snapshot()
        InfoBar.success("Saved", "Your settings have been saved.",
                        duration=4000, position=InfoBarPosition.TOP, parent=self)
        return True
