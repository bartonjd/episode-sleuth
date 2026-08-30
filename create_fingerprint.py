#!/usr/bin/env python3
"""
create_fingerprint.py
=====================
Build a phonetic fingerprint database from subtitles.

Input can be either:
  * a show/movie query to download from OpenSubtitles.org, e.g. "Matlock 1986"
  * a directory (or single file) of local .srt / .vtt subtitle files

Examples
--------
  # Download Matlock (1986) subtitles and fingerprint them
  python create_fingerprint.py --show "Matlock 1986"

  # Fingerprint a folder of local subtitle files
  python create_fingerprint.py --dir /path/to/subs --title "Matlock" --year 1986

  # Inspect the database
  python create_fingerprint.py --list

  # Re-process everything, even files already in the database
  python create_fingerprint.py --dir /path/to/subs --force

Note
----
Files that have already been fingerprinted are skipped automatically (matched by
their source file path). Pass --force to re-process them anyway.
"""

import os
import sys
import argparse
import logging

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    MediaInfo, fingerprint_text, phonetic_token_stream,
)
import subtitle_utils as su


def fingerprint_subtitle_file(path: str, db: FingerprintDB, fp_cfg: FingerprintConfig,
                              default_title=None, default_year=None,
                              media_type_override=None, reindex=True) -> int:
    """Parse one subtitle file and add its fingerprints to the DB."""
    info = su.parse_filename_metadata(path, default_title, default_year)
    if media_type_override:
        info.media_type = media_type_override

    try:
        cues = su.parse_subtitle_file(path)
    except Exception as exc:
        logging.error("Could not parse %s: %s", path, exc)
        return 0

    if not cues:
        logging.warning("No dialogue cues found in %s", path)
        return 0

    if reindex:
        db.clear_media(info)
    media_id = db.get_or_create_media(info)

    total = 0
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
        if len(rows) >= 5000:
            total += db.add_fingerprints(media_id, rows)
            rows = []
    if rows:
        total += db.add_fingerprints(media_id, rows)

    if token_stream:
        db.add_token_stream(media_id, token_stream, token_starts)

    logging.info("  + %-45s -> %5d fingerprints, %5d tokens",
                 info.label(), total, len(token_stream))
    return total


def run_directory(directory, db, fp_cfg, title, year, media_type, force=False):
    if os.path.isfile(directory):
        files = [directory]
    else:
        files = su.find_subtitle_files(directory)
    if not files:
        logging.error("No .srt/.vtt files found in %s", directory)
        return 0, 0, 0
    logging.info("Found %d subtitle file(s)", len(files))
    grand = processed = skipped = 0
    for i, f in enumerate(files, 1):
        already = db.file_has_phonetic(f)
        if already and not force:
            logging.info("Skipping already processed file: %s", os.path.basename(f))
            skipped += 1
            continue
        if already and force:
            logging.info("Re-processing file: %s", os.path.basename(f))
        logging.info("[%d/%d] Processing %s", i, len(files), os.path.basename(f))
        grand += fingerprint_subtitle_file(f, db, fp_cfg, title, year, media_type)
        processed += 1
    return grand, processed, skipped


def run_show(query, db, fp_cfg, cfg, limit, media_type, year_override=None, force=False):
    # The year may come either inside the --show string ("Matlock 1986") or
    # via the separate --year flag. Combine both so the API year filter works.
    title, year = su._parse_query(query)
    if year_override:
        year = year_override
    effective_query = f"{title} {year}".strip() if year else title

    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
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
    grand = processed = skipped = 0
    for i, f in enumerate(files, 1):
        already = db.file_has_phonetic(f)
        if already and not force:
            logging.info("Skipping already processed file: %s", os.path.basename(f))
            skipped += 1
            continue
        if already and force:
            logging.info("Re-processing file: %s", os.path.basename(f))
        logging.info("[%d/%d] Processing %s", i, len(files), os.path.basename(f))
        grand += fingerprint_subtitle_file(f, db, fp_cfg, title, year, media_type)
        processed += 1
    return grand, processed, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create phonetic fingerprint DB from subtitles.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", help="Show/movie to download from OpenSubtitles, e.g. 'Matlock 1986'")
    group.add_argument("--dir", help="Directory or file of local .srt/.vtt subtitles")
    group.add_argument("--file", help="Single .srt/.vtt subtitle file to fingerprint")
    group.add_argument("--list", action="store_true", help="List media already in the database")
    parser.add_argument("--title", help="Override title for local files")
    parser.add_argument("--year", type=int, help="Override start year")
    parser.add_argument("--type", choices=["tv", "movie"], help="Force media type")
    parser.add_argument("--limit", type=int, default=5, help="Max subtitles to download (show mode)")
    parser.add_argument("--force", action="store_true",
                        help="Re-process files even if they are already in the "
                             "database. By default, files that have already been "
                             "fingerprinted are skipped automatically.")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--db", help="Override database path")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    db = FingerprintDB(db_path)

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
                year_override=args.year, force=args.force)
        elif args.dir or args.file:
            total, processed, skipped = run_directory(
                args.dir or args.file, db, fp_cfg, args.title, args.year,
                args.type, force=args.force)
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
