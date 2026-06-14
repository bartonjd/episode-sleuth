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
"""

import os
import sys
import argparse
import logging

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    MediaInfo, fingerprint_text,
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
    for (start_ms, end_ms, text) in cues:
        for (h, size) in fingerprint_text(text, fp_cfg):
            rows.append((h, size, start_ms, end_ms))
        if len(rows) >= 5000:
            total += db.add_fingerprints(media_id, rows)
            rows = []
    if rows:
        total += db.add_fingerprints(media_id, rows)

    logging.info("  + %-45s -> %5d fingerprints", info.label(), total)
    return total


def run_directory(directory, db, fp_cfg, title, year, media_type):
    if os.path.isfile(directory):
        files = [directory]
    else:
        files = su.find_subtitle_files(directory)
    if not files:
        logging.error("No .srt/.vtt files found in %s", directory)
        return 0
    logging.info("Found %d subtitle file(s)", len(files))
    grand = 0
    for i, f in enumerate(files, 1):
        logging.info("[%d/%d] Processing %s", i, len(files), os.path.basename(f))
        grand += fingerprint_subtitle_file(f, db, fp_cfg, title, year, media_type)
    return grand


def run_show(query, db, fp_cfg, cfg, limit, media_type, year_override=None):
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
        return 0
    grand = 0
    for i, f in enumerate(files, 1):
        logging.info("[%d/%d] Processing %s", i, len(files), os.path.basename(f))
        grand += fingerprint_subtitle_file(f, db, fp_cfg, title, year, media_type)
    return grand


MEDIA_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v",
              ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")


def run_acoustic(target, db, ac_cfg, title, year, media_type):
    """Build acoustic (Chromaprint) fingerprints from audio/video file(s)."""
    import glob
    import acoustic_fingerprint as af

    if os.path.isfile(target):
        files = [target]
    else:
        files = sorted(
            f for f in glob.glob(os.path.join(target, "**", "*"), recursive=True)
            if f.lower().endswith(MEDIA_EXTS)
        )
    if not files:
        logging.error("No audio/video files found in %s", target)
        return 0

    logging.info("Found %d media file(s) for acoustic fingerprinting", len(files))
    grand = 0
    for i, f in enumerate(files, 1):
        logging.info("[%d/%d] Processing %s", i, len(files), os.path.basename(f))
        info = su.parse_filename_metadata(f, title, year)
        if media_type:
            info.media_type = media_type
        grand += af.store_acoustic_fingerprints(db, info, f, ac_cfg, reindex=True)
    return grand


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create phonetic fingerprint DB from subtitles.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", help="Show/movie to download from OpenSubtitles, e.g. 'Matlock 1986'")
    group.add_argument("--dir", help="Directory or file of local .srt/.vtt subtitles "
                                     "(with --acoustic: directory of audio/video files)")
    group.add_argument("--file", help="Single audio/video file to acoustically fingerprint "
                                      "(implies --acoustic)")
    group.add_argument("--list", action="store_true", help="List media already in the database")
    parser.add_argument("--title", help="Override title for local files")
    parser.add_argument("--year", type=int, help="Override start year")
    parser.add_argument("--type", choices=["tv", "movie"], help="Force media type")
    parser.add_argument("--limit", type=int, default=5, help="Max subtitles to download (show mode)")
    parser.add_argument("--acoustic", action="store_true",
                        help="Treat --dir as audio/video files and build ACOUSTIC "
                             "(Chromaprint) fingerprints instead of phonetic ones")
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

        # Acoustic mode: --file always acoustic; --dir acoustic only with --acoustic
        if args.file or (args.dir and args.acoustic):
            import acoustic_fingerprint as af
            ac_cfg = af.AcousticConfig.from_config(cfg)
            target = args.file or args.dir
            total = run_acoustic(target, db, ac_cfg, args.title, args.year, args.type)
            print("\n" + "=" * 56)
            print(f"Done. Stored acoustic fingerprints for {total} segment(s).")
            print("Database stats:", db.stats())
            print(f"Database: {db_path}")
            return 0

        if args.show:
            total = run_show(args.show, db, fp_cfg, cfg, args.limit, args.type,
                             year_override=args.year)
        elif args.dir:
            total = run_directory(args.dir, db, fp_cfg, args.title, args.year, args.type)
        else:
            parser.print_help()
            return 1

        print("\n" + "=" * 56)
        print(f"Done. Added {total} fingerprints.")
        print("Database stats:", db.stats())
        print(f"Database: {db_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
