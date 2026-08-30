#!/usr/bin/env python3
"""
dvd_identifier_fluent.py
========================
A modern, Windows 11 "Fluent Design" desktop front end for the DVD episode
identifier, built with PySide6 and PySide6-Fluent-Widgets.

It is the sole desktop front end for the project (the earlier Tkinter version
has been retired). It talks to the same engine (identify_dvd_episodes.py +
fingerprint_core.py); the presentation is:

  * a dark, acrylic FluentWindow with an icon sidebar (Identify / Build library /
    Settings / Log),
  * rounded cards for every group of inputs,
  * a results table with a checkbox column, colour-coded rows (green / amber /
    red) and status icons,
  * a Cancel button and a live progress bar / running indicator,
  * a proper Settings page whose choices persist to gui_config.json.

Run it:
    python dvd_identifier_fluent.py
or double-click  fluent_launcher.bat  on Windows.

Install the one extra dependency first:
    pip install PySide6-Fluent-Widgets
"""
from __future__ import annotations

import os
import sys
import shutil
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import List, Optional

# Make project modules importable no matter what CWD Windows launches us from.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QColor, QBrush, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidgetItem, QHeaderView, QFileDialog, QSizePolicy, QAbstractItemView,
    QSpacerItem,
)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    PrimaryPushButton, PushButton, TransparentPushButton, CheckBox, LineEdit,
    SpinBox, DoubleSpinBox, SwitchButton, TableWidget, IndeterminateProgressBar,
    ProgressBar, StateToolTip, InfoBar, InfoBarPosition, TextEdit,
    BodyLabel, StrongBodyLabel, TitleLabel, SubtitleLabel, CaptionLabel,
    CardWidget, ComboBox, setTheme, setThemeColor, Theme, isDarkTheme,
    IndicatorPosition,
)

# ---- engine (identical to the CLI / Tkinter paths) ----
import identify_dvd_episodes as dvd
from identify_dvd_episodes import (
    FileResult, discover_media, identify_one, write_csv, write_json,
    episode_id_str,
)
from fingerprint_core import FingerprintDB, FingerprintConfig, load_config

from gui_config import GuiConfig

APP_TITLE = "DVD Episode Identifier"
DEFAULT_DB = os.path.join(HERE, "fingerprints.db")
DEFAULT_CONFIG = os.path.join(HERE, "config.json")

# Row tint colours (kept subtle so they read well on the dark theme).
COLOR_OK = QColor(45, 125, 60, 70)        # green  - confident
COLOR_MEDIUM = QColor(200, 150, 20, 70)   # amber  - usable but check
COLOR_REVIEW = QColor(200, 55, 55, 80)    # red    - needs review


# ---------------------------------------------------------------------------
# Logging bridge: route the engine's logging records to a Qt signal so the Log
# page (and nothing off the main thread) receives them safely.
# ---------------------------------------------------------------------------
class LogBridge(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: LogBridge):
        super().__init__()
        self.bridge = bridge

    def emit(self, record):
        try:
            self.bridge.message.emit(self.format(record))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Worker threads. Qt widgets may only be touched on the GUI thread, so workers
# run the heavy engine calls off-thread and communicate purely through signals.
# ---------------------------------------------------------------------------
class IdentifyWorker(QThread):
    rowReady = Signal(object)          # FileResult
    progress = Signal(int, int, int, str)  # current, total, percent, filename
    finishedOk = Signal(int, int)      # total, needing_review
    failed = Signal(str)
    wasCancelled = Signal(int)         # how many completed before cancel

    def __init__(self, db_path: str, source: str, params: SimpleNamespace):
        super().__init__()
        self.db_path = db_path
        self.source = source
        self.params = params
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            cfg = load_config(self.params.config_path)
            # Honour the Vosk model size chosen in Settings (small vs large).
            cfg.setdefault("stt", {})
            model_size = getattr(self.params, "vosk_model_size", "small")
            cfg["stt"]["model_size"] = model_size
            if model_size == "large":
                # Let the size selector resolve the model dir; drop any hardcoded
                # small path so the large model is used when it is downloaded.
                cfg["stt"].pop("vosk_model_path", None)
            fp_cfg = FingerprintConfig.from_config(cfg)

            args = SimpleNamespace(
                points=self.params.points,
                sample_len=self.params.sample_len,
                review_confidence=self.params.review_confidence,
                runtime_tolerance=self.params.runtime_tolerance,
                show_title=getattr(self.params, "show_title", None) or None,
            )

            if os.path.isdir(self.source):
                media = discover_media(self.source)
            else:
                media = [self.source]
            if not media:
                self.failed.emit("No media files found to identify.")
                return

            # One shared speech-to-text engine for every worker thread. Vosk's
            # Model is safe to share (each transcription builds its own
            # recogniser internally).
            try:
                import stt_utils
                transcriber = stt_utils.get_transcriber(cfg)
            except Exception as exc:
                self.failed.emit(
                    f"Could not initialise the speech-to-text engine: {exc}\n"
                    "See INSTALL_WINDOWS.md / README to download a Vosk model.")
                return

            workers = max(1, int(getattr(self.params, "max_workers", 4)))
            logging.info("Identifying %d file(s) against %s with %d worker(s)",
                         len(media), os.path.basename(self.db_path), workers)

            results: List[FileResult] = []
            done = 0
            total = len(media)

            def _work(path):
                # Each call opens its own DB connection (thread-safe).
                return identify_one(path, self.db_path, fp_cfg, cfg, args,
                                    transcriber, None)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {}
                for path in media:
                    if self._cancel:
                        break
                    futs[pool.submit(_work, path)] = path
                for fut in as_completed(futs):
                    path = futs[fut]
                    done += 1
                    pct = int(done * 100 / total) if total else 0
                    self.progress.emit(done, total, pct, os.path.basename(path))
                    if self._cancel:
                        continue
                    try:
                        r = fut.result()
                        results.append(r)
                        self.rowReady.emit(r)
                    except Exception as exc:  # keep going on a single bad file
                        logging.error("ERROR on %s: %s",
                                      os.path.basename(path), exc)

            if self._cancel:
                logging.info("Identification cancelled by user.")
                self.wasCancelled.emit(len(results))
                return

            review = sum(1 for r in results if r.needs_review)
            self.finishedOk.emit(len(results), review)
        except Exception as exc:
            self.failed.emit(str(exc))


class BuildWorker(QThread):
    output = Signal(str)
    done = Signal(int)

    def __init__(self, cmd: List[str]):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            kwargs = dict(cwd=HERE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1)
            # Never flash a console window on Windows for the child process.
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(self.cmd, **kwargs)
            assert proc.stdout is not None
            for line in proc.stdout:
                self.output.emit(line.rstrip())
                logging.info(line.rstrip())
            proc.wait()
            self.output.emit(f"[exit code {proc.returncode}]")
            self.done.emit(proc.returncode)
        except Exception as exc:
            self.output.emit(f"ERROR: {exc}")
            self.done.emit(-1)


# ---------------------------------------------------------------------------
# Small helper widgets
# ---------------------------------------------------------------------------
class Card(CardWidget):
    """A rounded card with a title and a vertical content area."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(20, 16, 20, 16)
        self._v.setSpacing(12)
        if title:
            self._v.addWidget(StrongBodyLabel(title))

    def add(self, *widgets, spacing: int = 8):
        row = QHBoxLayout()
        row.setSpacing(spacing)
        for w in widgets:
            row.addWidget(w)
        self._v.addLayout(row)
        return row

    def addLayout(self, layout):
        self._v.addLayout(layout)

    def addWidget(self, w):
        self._v.addWidget(w)


def _path_row(placeholder: str):
    """A LineEdit that expands to fill available width."""
    le = LineEdit()
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return le


# ---------------------------------------------------------------------------
# Identify page
# ---------------------------------------------------------------------------
class IdentifyInterface(QWidget):
    COLS = ["", "File", "Episode", "Title", "Conf.", "Method", "Agree", "Notes"]

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
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)      # Title
        for c in (2, 4, 5, 6):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)      # Notes
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

        # tint + auto-check high-confidence rows
        if r.needs_review or conf < float(self.review_spin.value()):
            tint = COLOR_REVIEW
            auto_check = False
        elif conf >= 0.7:
            tint = COLOR_OK
            auto_check = True
        else:
            tint = COLOR_MEDIUM
            auto_check = True

        # checkbox column (stores the result index in UserRole)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if auto_check else Qt.Unchecked)
        chk.setData(Qt.UserRole, len(self.results) - 1)
        self.table.setItem(row, 0, chk)

        notes = data["notes"] or ("ok" if not r.needs_review else "review")
        values = [
            data["filename"],
            data["episode_id"],
            data["title"],
            f"{conf:.0%}",
            data["method"],
            data["agreement"],
            notes,
        ]
        for col, text in enumerate(values, start=1):
            item = QTableWidgetItem(str(text))
            if col in (2, 4, 6):  # episode, conf, agree centered
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
            newname = f"{show} - {episode_id_str(g.season, g.episode)}{ext}"
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


# ---------------------------------------------------------------------------
# Build library page
# ---------------------------------------------------------------------------
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
        cmd = [sys.executable, os.path.join(HERE, "create_fingerprint.py"),
               flag, path, "--db", self.win.current_db_path() or DEFAULT_DB]
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


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------
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
        stt_grid.setHorizontalSpacing(24)
        stt_grid.setVerticalSpacing(10)
        stt_grid.addWidget(BodyLabel("Model size"), 0, 0)
        self.model_combo = ComboBox()
        self._model_labels = ["Small (39 MB)", "Large (1.8 GB)"]
        self._model_values = ["small", "large"]
        self.model_combo.addItems(self._model_labels)
        cur_size = self.cfg.get("vosk_model_size", "small")
        idx = self._model_values.index(cur_size) if cur_size in self._model_values else 0
        self.model_combo.setCurrentIndex(idx)
        stt_grid.addWidget(self.model_combo, 1, 0)
        stt_grid.addWidget(
            CaptionLabel("The large model is far more accurate on clean DVD-rip "
                         "audio but uses ~1.8 GB. It downloads automatically the "
                         "first time it is used."), 2, 0)
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

    def _save(self):
        self.cfg.update(
            db_path=self.db_edit.text().strip(),
            engine_config_path=self.eng_edit.text().strip(),
            theme=self.theme_combo.currentText(),
            max_workers=int(self.workers_spin.value()),
            vosk_model_size=self._model_values[self.model_combo.currentIndex()],
        )
        self.cfg.save()
        InfoBar.success("Saved", "Your settings have been saved.",
                        duration=4000, position=InfoBarPosition.TOP, parent=self)


# ---------------------------------------------------------------------------
# Log page
# ---------------------------------------------------------------------------
class LogInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.setObjectName("logInterface")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Log"))
        root.addWidget(CaptionLabel(
            "Live engine output for the current session."))

        bar = QHBoxLayout()
        bar.addStretch(1)
        clear_btn = PushButton("Clear", self, FIF.DELETE)
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        bar.addWidget(clear_btn)
        root.addLayout(bar)

        self.log_view = TextEdit()
        self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, 1)

    def append(self, line: str):
        self.log_view.append(line)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.gui_cfg = GuiConfig()

        # theme first so child widgets pick up the palette
        self.apply_theme(self.gui_cfg.get("theme", "Dark"))
        setThemeColor(self.gui_cfg.get("theme_color", "#0078d4"))

        self.resize(1080, 760)
        self.setWindowTitle(APP_TITLE)
        try:
            self.setWindowIcon(QIcon(FIF.ALBUM.path()))
        except Exception:
            pass

        # logging bridge -> Log page
        self.log_bridge = LogBridge()
        self._install_logging()

        # pages
        self.identify_interface = IdentifyInterface(self)
        self.build_interface = BuildInterface(self)
        self.settings_interface = SettingsInterface(self)
        self.log_interface = LogInterface(self)
        self.log_bridge.message.connect(self.log_interface.append)

        self.addSubInterface(self.identify_interface, FIF.SEARCH, "Identify")
        self.addSubInterface(self.build_interface, FIF.LIBRARY, "Build library")
        self.addSubInterface(self.settings_interface, FIF.SETTING, "Settings",
                             NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.log_interface, FIF.COMMAND_PROMPT, "Log",
                             NavigationItemPosition.BOTTOM)

        self.navigationInterface.setExpandWidth(220)

    # ----- helpers used by pages -----
    def current_db_path(self) -> str:
        p = self.gui_cfg.get("db_path", "")
        if p:
            return p
        return DEFAULT_DB if os.path.exists(DEFAULT_DB) else ""

    def current_engine_config(self) -> str:
        return self.gui_cfg.get("engine_config_path", "")

    def apply_theme(self, name: str):
        mapping = {"Dark": Theme.DARK, "Light": Theme.LIGHT, "Auto": Theme.AUTO}
        setTheme(mapping.get(name, Theme.DARK))

    def _install_logging(self):
        handler = QtLogHandler(self.log_bridge)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers = [h for h in root.handlers if not isinstance(h, QtLogHandler)]
        root.addHandler(handler)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
