#!/usr/bin/env python3
"""Worker threads for the GUI.

Qt widgets may only be touched on the GUI thread, so these workers run the heavy
engine calls off-thread and communicate purely through signals.
"""
from __future__ import annotations

import os
import sys
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import List

from PySide6.QtCore import QThread, Signal

from audio_fingerprint.gui.constants import HERE

# ---- engine (identical to the CLI path) ----
from engine import (
    FileResult, discover_media, identify_one,
)
from fingerprint_core import FingerprintConfig, load_config

# Speech-to-text helpers (Vosk model download + lookup). Imported defensively so
# the GUI still launches even if an optional dependency is missing.
try:
    import stt_utils
    from stt_utils import ModelDownloadError
except Exception:  # pragma: no cover - only if pydub/vosk deps are absent
    stt_utils = None

    class ModelDownloadError(Exception):
        pass


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


class ModelDownloadWorker(QThread):
    """Downloads (or re-downloads) a Vosk model off the GUI thread.

    Emits byte-level progress so the Settings page can show a progress bar, and
    a clear success/failure result. ``force=True`` always re-fetches the pinned
    latest published build, which is how the "Re-download / Update" button
    refreshes an already-installed model.
    """
    progress = Signal(int, int)   # downloaded_bytes, total_bytes (0 = unknown)
    finishedOk = Signal(str)      # local model directory path
    failed = Signal(str)          # human-readable error message

    def __init__(self, model_size: str, force: bool = True):
        super().__init__()
        self.model_size = model_size
        self.force = force
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if stt_utils is None:
            self.failed.emit(
                "The speech-to-text module could not be loaded. Please make "
                "sure the app's dependencies (pydub, vosk) are installed.")
            return
        try:
            path = stt_utils.download_vosk_model(
                self.model_size,
                progress=lambda d, t: self.progress.emit(d, t),
                force=self.force,
                cancel_check=lambda: self._cancel,
            )
            self.finishedOk.emit(path)
        except ModelDownloadError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # unexpected - still surface something useful
            self.failed.emit(f"Unexpected error while downloading the model: {exc}")
