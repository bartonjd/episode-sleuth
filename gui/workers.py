#!/usr/bin/env python3
"""Worker threads for the GUI.

Qt widgets may only be touched on the GUI thread, so these workers run the heavy
engine calls off-thread and communicate purely through signals.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import List

from PySide6.QtCore import QThread, Signal

# ---- engine (identical to the CLI path) ----
from engine import (
    FileResult,
    discover_media,
    identify_one,
)
from fingerprint_core import FingerprintConfig, FingerprintDB, load_config

# Speech-to-text helpers (Vosk model download + lookup). Imported defensively so
# the GUI still launches even if an optional dependency is missing.
try:
    import stt_utils
    from stt_utils import ModelDownloadError
except Exception:  # pragma: no cover - only if pydub/vosk deps are absent
    stt_utils = None

    class ModelDownloadError(Exception):
        pass


def _resolve_primary_language(label):
    """Translate the Settings "Primary Language" choice into an ISO code.

    "Auto-detect" (or a missing/empty value) returns None, which leaves the
    engine on its English/metaphone default - the right behaviour for a
    single-language English library. Any other choice is normalised to an ISO
    code ("en"/"es"/"fr"/"de"/"other") so the query is encoded the same way the
    reference library was built. Any failure falls back to None so a bad value
    never blocks an identify run.
    """
    if not label or str(label).strip().lower() in ("auto-detect", "auto"):
        return None
    try:
        from engine.language_utils import normalise_language
        return normalise_language(label)
    except Exception:
        return None


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
            # Honour the Primary Language chosen in Settings. A specific language
            # (not "Auto-detect") sets the phonetic strategy and the Vosk model
            # language for the whole identify run so the query is encoded the
            # same way the reference library was built. "Auto-detect" leaves the
            # language unset (English/metaphone default), which is the right
            # behaviour for identifying against a single-language English library.
            _lang = _resolve_primary_language(
                getattr(self.params, "primary_language", None))
            if _lang:
                cfg.setdefault("fingerprint", {})["language"] = _lang
                cfg["stt"]["language"] = _lang
            fp_cfg = FingerprintConfig.from_config(cfg)

            args = SimpleNamespace(
                points=self.params.points,
                sample_len=self.params.sample_len,
                review_confidence=self.params.review_confidence,
                runtime_tolerance=self.params.runtime_tolerance,
                show_title=getattr(self.params, "show_title", None) or None,
            )

            if os.path.isdir(self.source):
                # Prefer the extension list chosen in Settings; fall back to the
                # engine config.json default when the GUI has not set one.
                media_exts = getattr(self.params, "media_extensions", None) \
                    or cfg.get("identify", {}).get("media_extensions")
                media = discover_media(self.source, media_exts)
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
                                    transcriber, None,
                                    cancel_check=lambda: self._cancel)

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


class LibraryBuildWorker(QThread):
    """Builds the phonetic reference library in-process (no subprocess).

    Runs :func:`cli.build_fingerprints.run_directory` directly on this worker
    thread. The previous implementation shelled out to
    ``sys.executable -m cli.build_fingerprints``; in a frozen PyInstaller build
    ``sys.executable`` is the app's own ``.exe``, which ignores the ``-m`` flag
    and simply launched a *second copy of the whole GUI* - the "dual window" bug.
    Building in-process fixes that and lets us report live per-file progress and
    elapsed time.

    ``use_processes=False`` is passed to the builder so it never uses a
    ProcessPoolExecutor: under a frozen executable, multiprocessing re-launches
    the bundled ``.exe`` for each child, which would spawn extra windows. Thread
    or sequential compute keeps everything inside this one process.
    """
    output = Signal(str)                 # human-readable log line
    progress = Signal(int, int, str)     # done, total, current filename
    # exit_code (0 = ok), fingerprints_added, files_processed, files_skipped
    done = Signal(int, int, int, int)

    def __init__(self, path: str, db_path: str, config_path=None,
                 show_title: str = "", overwrite: bool = False,
                 fetch_duration: bool = False, workers: int = 4,
                 language=None):
        super().__init__()
        self.path = path
        self.db_path = db_path
        self.config_path = config_path or None
        self.show_title = show_title
        self.overwrite = overwrite
        self.fetch_duration = fetch_duration
        self.workers = max(1, int(workers))
        self.language = language

    def run(self):
        try:
            import dataclasses

            from cli.build_fingerprints import run_directory

            cfg = load_config(self.config_path)
            fp_cfg = FingerprintConfig.from_config(cfg)
            # Apply the Primary Language choice (if any) so the library is
            # encoded consistently; an unset/auto value leaves per-file
            # auto-detection to the builder.
            if self.language:
                try:
                    from engine.language_utils import normalise_language
                    _lang = normalise_language(self.language)
                    if _lang:
                        fp_cfg = dataclasses.replace(fp_cfg, language=_lang)
                except Exception:
                    pass

            try:
                db = FingerprintDB(self.db_path)
            except (ValueError, OSError) as exc:
                self.output.emit(f"ERROR: could not open database at "
                                 f"{self.db_path}: {exc}")
                self.done.emit(-1, 0, 0, 0)
                return

            def _progress(done_n, total_n, path):
                name = os.path.basename(path)
                self.progress.emit(done_n, total_n, name)
                self.output.emit(f"[{done_n}/{total_n}] {name}")

            try:
                total, processed, skipped = run_directory(
                    self.path, db, fp_cfg, None, None, None,
                    force=self.overwrite,
                    show_title=self.show_title or None,
                    workers=self.workers,
                    fetch_duration=self.fetch_duration,
                    progress=_progress,
                    use_processes=False)
            finally:
                db.close()

            self.output.emit(
                f"Done. Added {total} fingerprints - processed {processed} "
                f"new file(s), skipped {skipped} existing.")
            self.done.emit(0, total, processed, skipped)
        except Exception as exc:
            logging.exception("Library build failed")
            self.output.emit(f"ERROR: {exc}")
            self.done.emit(-1, 0, 0, 0)


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
