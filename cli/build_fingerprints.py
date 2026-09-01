#!/usr/bin/env python3
"""
build_fingerprints.py  (entry point: dvd-fingerprint)
=====================================================
Build a phonetic fingerprint database from subtitles.

This is the reference-library builder (formerly ``create_fingerprint.py``). It
reads subtitle files, turns their dialogue into phonetic fingerprints and stores
them in the database that ``dvd-identify`` matches against. It does NOT do any
speech-to-text - that only happens at identification time.

Input can be either:
  * a show/movie query to download from OpenSubtitles.org, e.g. "Matlock 1986"
  * a directory (or single file) of local .srt / .vtt subtitle files

Examples
--------
  # Download Matlock (1986) subtitles and fingerprint them
  python -m audio_fingerprint.cli.build_fingerprints --show "Matlock 1986"

  # Fingerprint a folder of local subtitle files
  python -m audio_fingerprint.cli.build_fingerprints --dir /path/to/subs \
      --title "Matlock" --year 1986

  # Inspect the database
  python -m audio_fingerprint.cli.build_fingerprints --list

  # Re-process everything, even files already in the database
  python -m audio_fingerprint.cli.build_fingerprints --dir /path/to/subs --force

Note
----
Files that have already been fingerprinted are skipped automatically (matched by
their source file path). Pass --force to re-process them anyway.
"""

import os
import sys
import argparse
import logging
from concurrent.futures import (
    ThreadPoolExecutor, ProcessPoolExecutor, as_completed,
)
from concurrent.futures.process import BrokenProcessPool

# Ensure the flat engine modules on the project root are importable, and use the
# project root (not this cli/ directory) to anchor the downloads dir and any
# relative DB path - preserving the behaviour of the original script.
from . import _ROOT as PROJECT_ROOT  # noqa: F401

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    MediaInfo, fingerprint_text, phonetic_token_stream,
)
import subtitle_utils as su

# Default number of parallel workers for building (matches the identifier).
DEFAULT_BUILD_WORKERS = 4


def _compute_fingerprints(path: str, fp_cfg: FingerprintConfig,
                          default_title=None, default_year=None,
                          media_type_override=None, show_title=None):
    """Parse one subtitle file and compute its fingerprints - NO database access.

    This is the CPU / IO heavy half of processing a subtitle file (reading the
    file, parsing cues, phonetic encoding). It touches no shared state, so it is
    safe to run in a worker thread. Returns a tuple
    ``(info, rows, token_stream, token_starts)`` ready to be handed to
    :func:`_store_fingerprints`, or ``None`` if the file could not be parsed or
    contained no dialogue.
    """
    info = su.parse_filename_metadata(path, default_title, default_year)
    if media_type_override:
        info.media_type = media_type_override
    if show_title:
        info.show_title = show_title
        info.media_type = "tv"

    try:
        cues = su.parse_subtitle_file(path)
    except Exception as exc:
        logging.error("Could not parse %s: %s", path, exc)
        return None

    if not cues:
        logging.warning("No dialogue cues found in %s", path)
        return None

    rows = []
    # Accumulate the full ordered phonetic token stream (with a parallel list of
    # cue start times) so the fuzzy / order-preserving matcher can be used later.
    token_stream: list = []
    token_starts: list = []
    for (start_ms, end_ms, text) in cues:
        for (h, size) in fingerprint_text(text, fp_cfg):
            rows.append((h, size, start_ms, end_ms))
        for tok in phonetic_token_stream(text, fp_cfg):
            token_stream.append(tok)
            token_starts.append(start_ms)

    return info, rows, token_stream, token_starts


def _store_fingerprints(db: FingerprintDB, computed, reindex: bool = True) -> int:
    """Write pre-computed fingerprints to the DB. MUST run on a single thread.

    SQLite writes on the shared connection are not safe to issue concurrently,
    so all callers funnel their writes through here on the orchestrating thread
    while the heavy parsing/encoding happens in parallel via
    :func:`_compute_fingerprints`.
    """
    info, rows, token_stream, token_starts = computed
    if reindex:
        db.clear_media(info)
    media_id = db.get_or_create_media(info)

    total = 0
    for start in range(0, len(rows), 5000):
        total += db.add_fingerprints(media_id, rows[start:start + 5000])

    if token_stream:
        db.add_token_stream(media_id, token_stream, token_starts)

    logging.info("  + %-45s -> %5d fingerprints, %5d tokens",
                 info.label(), total, len(token_stream))
    return total


def fingerprint_subtitle_file(path: str, db: FingerprintDB, fp_cfg: FingerprintConfig,
                              default_title=None, default_year=None,
                              media_type_override=None, reindex=True,
                              show_title=None) -> int:
    """Parse one subtitle file and add its fingerprints to the DB.

    ``show_title`` (optional) associates the episode with a TV show, overriding
    the show title heuristically parsed from the filename. Passing it for a whole
    folder (see ``--show-title``) scopes later identification to that one show,
    which sharply reduces cross-show / cross-season false matches.

    Kept for backward compatibility: it simply computes the fingerprints and
    stores them in one call (see :func:`_compute_fingerprints` /
    :func:`_store_fingerprints` for the split used by the parallel builder).
    """
    computed = _compute_fingerprints(path, fp_cfg, default_title, default_year,
                                     media_type_override, show_title)
    if computed is None:
        return 0
    return _store_fingerprints(db, computed, reindex=reindex)


def _run_sequential(to_process, db, fp_cfg, title, year, media_type,
                    show_title, progress, done_offset=0, total=None):
    """Process ``to_process`` one file at a time on the current thread."""
    total = total if total is not None else len(to_process)
    grand = processed = 0
    for i, f in enumerate(to_process, 1):
        done = done_offset + i
        logging.info("[%d/%d] Processing %s", done, total, os.path.basename(f))
        computed = _compute_fingerprints(f, fp_cfg, title, year, media_type,
                                         show_title)
        if computed is not None:
            grand += _store_fingerprints(db, computed)
        processed += 1
        if progress:
            progress(done, total, f)
    return grand, processed


def _process_files(files, db, fp_cfg, title, year, media_type, force=False,
                   show_title=None, workers=DEFAULT_BUILD_WORKERS, progress=None):
    """Fingerprint a list of subtitle files, in parallel when it helps.

    Parsing + phonetic encoding for each file (the CPU-heavy part) runs across a
    pool of workers, while the SQLite writes are serialised on this thread so the
    single connection is never used concurrently.

    A :class:`ProcessPoolExecutor` is used for the compute stage because phonetic
    encoding is pure-Python, CPU-bound work that the GIL would otherwise
    serialise - processes give a near-linear speedup on multi-core machines,
    whereas threads would not. (This differs from the batch *identifier*, whose
    work is dominated by native ffmpeg/STT calls that already release the GIL, so
    threads suffice there.) If a process pool cannot be created or breaks mid-run
    (restricted or frozen environments), it transparently falls back to a thread
    pool and then to sequential processing, so a build never fails outright.

    ``workers == 1`` (or a single file) keeps the original fully-sequential
    behaviour. ``progress`` (optional) is called ``progress(done, total, path)``
    after each file finishes. Per-file errors are logged and skipped so one bad
    subtitle cannot abort the whole batch.

    Returns ``(grand_total_fingerprints, processed_count, skipped_count)``.
    """
    grand = processed = skipped = 0

    # DB read (skip check) happens up front on this thread.
    to_process = []
    for f in files:
        already = db.file_has_phonetic(f)
        if already and not force:
            logging.info("Skipping already processed file: %s", os.path.basename(f))
            skipped += 1
            continue
        if already and force:
            logging.info("Re-processing file: %s", os.path.basename(f))
        to_process.append(f)

    total = len(to_process)
    if total == 0:
        return grand, processed, skipped

    # Sequential path (backward compatible, and used for a single file where a
    # worker pool would only add overhead).
    if workers <= 1 or total == 1:
        g, p = _run_sequential(to_process, db, fp_cfg, title, year, media_type,
                               show_title, progress, total=total)
        return grand + g, processed + p, skipped

    # Parallel path: compute across worker processes, store serially as results
    # arrive. Fall back gracefully if the pool cannot start or breaks mid-run.
    logging.info("Building with %d parallel workers", workers)
    for pool_cls, kind in ((ProcessPoolExecutor, "processes"),
                           (ThreadPoolExecutor, "threads")):
        completed = set()
        done = 0
        try:
            with pool_cls(max_workers=workers) as pool:
                future_to_file = {
                    pool.submit(_compute_fingerprints, f, fp_cfg, title, year,
                                media_type, show_title): f
                    for f in to_process
                }
                for future in as_completed(future_to_file):
                    f = future_to_file[future]
                    done += 1
                    try:
                        computed = future.result()
                    except Exception as exc:  # per-file safety net
                        logging.error("Failed to process %s: %s",
                                      os.path.basename(f), exc)
                        computed = None
                    logging.info("[%d/%d] Processed %s", done, total,
                                 os.path.basename(f))
                    if computed is not None:
                        grand += _store_fingerprints(db, computed)
                    processed += 1
                    completed.add(f)
                    if progress:
                        progress(done, total, f)
            return grand, processed, skipped
        except BrokenProcessPool as exc:
            # The pool died (e.g. sandbox/frozen build). Redo whatever did not
            # finish on the next strategy (threads), then sequential.
            logging.warning("%s pool unavailable (%s); falling back.", kind, exc)
            remaining = [f for f in to_process if f not in completed]
            if not remaining:
                return grand, processed, skipped
            to_process = remaining
            total = processed + len(remaining)
            continue

    # Last resort: sequential for anything still not processed.
    g, p = _run_sequential(to_process, db, fp_cfg, title, year, media_type,
                           show_title, progress, done_offset=processed,
                           total=processed + len(to_process))
    return grand + g, processed + p, skipped


def run_directory(directory, db, fp_cfg, title, year, media_type, force=False,
                  show_title=None, workers=DEFAULT_BUILD_WORKERS):
    if os.path.isfile(directory):
        files = [directory]
    else:
        files = su.find_subtitle_files(directory)
    if not files:
        logging.error("No .srt/.vtt files found in %s", directory)
        return 0, 0, 0
    logging.info("Found %d subtitle file(s)", len(files))
    if show_title:
        logging.info("Associating all files with TV show: %s", show_title)
    return _process_files(files, db, fp_cfg, title, year, media_type,
                          force=force, show_title=show_title, workers=workers)


def run_show(query, db, fp_cfg, cfg, limit, media_type, year_override=None,
             force=False, show_title=None, workers=DEFAULT_BUILD_WORKERS):
    # The year may come either inside the --show string ("Matlock 1986") or
    # via the separate --year flag. Combine both so the API year filter works.
    title, year = su._parse_query(query)
    if year_override:
        year = year_override
    effective_query = f"{title} {year}".strip() if year else title

    out_dir = os.path.join(
        PROJECT_ROOT,
        cfg.get("opensubtitles", {}).get("download_dir", "downloads"),
        effective_query.replace(" ", "_"),
    )
    logging.info("Downloading subtitles for '%s' ...", effective_query)
    files = su.download_opensubtitles(effective_query, out_dir, cfg, limit=limit,
                                      media_type=media_type)
    if not files:
        logging.error("No subtitles downloaded for '%s'. "
                      "Try --dir with local files instead.", effective_query)
        return 0, 0, 0
    return _process_files(files, db, fp_cfg, title, year, media_type,
                          force=force, show_title=show_title or title,
                          workers=workers)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create phonetic fingerprint DB from subtitles.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", help="Show/movie to download from OpenSubtitles, e.g. 'Matlock 1986'")
    group.add_argument("--dir", help="Directory or file of local .srt/.vtt subtitles")
    group.add_argument("--file", help="Single .srt/.vtt subtitle file to fingerprint")
    group.add_argument("--list", action="store_true", help="List media already in the database")
    parser.add_argument("--title", help="Override title for local files")
    parser.add_argument("--show-title", dest="show_title",
                        help="Associate all imported episodes with this TV show "
                             "(batch import mode). Scopes later identification to "
                             "the one show and reduces cross-show false matches.")
    parser.add_argument("--year", type=int, help="Override start year")
    parser.add_argument("--type", choices=["tv", "movie"], help="Force media type")
    parser.add_argument("--limit", type=int, default=5, help="Max subtitles to download (show mode)")
    parser.add_argument("--force", action="store_true",
                        help="Re-process files even if they are already in the "
                             "database. By default, files that have already been "
                             "fingerprinted are skipped automatically.")
    parser.add_argument("--workers", type=int, default=DEFAULT_BUILD_WORKERS,
                        help="Number of parallel worker threads used to parse "
                             "and fingerprint subtitle files (default: "
                             f"{DEFAULT_BUILD_WORKERS}). Parsing/encoding runs "
                             "concurrently while database writes stay "
                             "serialised, so large libraries build much faster. "
                             "Use 1 to force fully sequential processing.")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--db", help="Override database path")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(PROJECT_ROOT, db_path)
    # FingerprintDB validates/creates the parent directory; surface a clear
    # message instead of a raw traceback if that fails (e.g. permission denied).
    try:
        db = FingerprintDB(db_path)
    except (ValueError, OSError) as exc:
        logging.error("Could not open database at %s: %s", db_path, exc)
        print(f"ERROR: could not open database at {db_path}: {exc}")
        return 2

    try:
        if args.list:
            rows = db.list_media()
            if not rows:
                print("Database is empty.")
            else:
                print(f"{'Title':30} {'Year':6} {'Type':6} {'S':>3} {'E':>3}")
                print("-" * 56)
                for r in rows:
                    print(f"{r['title'][:30]:30} {str(r['year'] or ''):6} "
                          f"{r['media_type']:6} {str(r['season'] or ''):>3} "
                          f"{str(r['episode'] or ''):>3}")
            print("\nStats:", db.stats())
            return 0

        if args.show:
            total, processed, skipped = run_show(
                args.show, db, fp_cfg, cfg, args.limit, args.type,
                year_override=args.year, force=args.force,
                show_title=args.show_title, workers=args.workers)
        elif args.dir or args.file:
            total, processed, skipped = run_directory(
                args.dir or args.file, db, fp_cfg, args.title, args.year,
                args.type, force=args.force, show_title=args.show_title,
                workers=args.workers)
        else:
            parser.print_help()
            return 1

        print("\n" + "=" * 56)
        print(f"Done. Added {total} fingerprints.")
        print(f"Processed {processed} new files, skipped {skipped} existing files")
        print("Database stats:", db.stats())
        print(f"Database: {db_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
