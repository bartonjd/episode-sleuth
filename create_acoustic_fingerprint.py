#!/usr/bin/env python3
"""
create_acoustic_fingerprint.py
==============================
Build the ACOUSTIC (Chromaprint/AcoustID) fingerprint database from video or
audio files. This is the sound-based counterpart to create_fingerprint.py
(which fingerprints subtitle dialogue).

Use this to index theme music, sound effects and scene audio so the system can
identify a show even when no one is speaking.

Examples
--------
  # Fingerprint a single episode video
  python create_acoustic_fingerprint.py --file "Matlock.1986.S01E03.mkv" \
        --title "Matlock" --year 1986 --season 1 --episode 3

  # Batch-fingerprint a folder of episodes (metadata auto-detected from names)
  python create_acoustic_fingerprint.py --dir /media/matlock --title "Matlock" --year 1986

  # Identify a clip against the acoustic DB (don't store it)
  python create_acoustic_fingerprint.py --file clip.mp4 --identify

  # List media that have acoustic fingerprints
  python create_acoustic_fingerprint.py --list

  # Re-process files even if already in the database
  python create_acoustic_fingerprint.py --dir /media/matlock --force

Note
----
Files that already have acoustic fingerprints are skipped automatically (matched
by their source file path). Pass --force to re-process them anyway.
"""

import os
import sys
import glob
import argparse
import logging

from fingerprint_core import (
    load_config, setup_logging, FingerprintDB, MediaInfo,
)
import subtitle_utils as su
import acoustic_fingerprint as af

MEDIA_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v",
              ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")


def _resolve_info(path, title, year, season, episode, media_type):
    info = su.parse_filename_metadata(path, title, year)
    if season is not None:
        info.season = season
    if episode is not None:
        info.episode = episode
    if media_type:
        info.media_type = media_type
    return info


def fingerprint_one(path, db, ac_cfg, title, year, season, episode, media_type):
    info = _resolve_info(path, title, year, season, episode, media_type)
    return af.store_acoustic_fingerprints(db, info, path, ac_cfg, reindex=True)


def identify_one(path, db, ac_cfg, threshold):
    logging.info("Identifying (acoustic) %s ...", os.path.basename(path))
    results = af.identify_file_acoustic(path, db, ac_cfg)
    print("\n" + "=" * 60)
    if not results or results[0].confidence < threshold:
        print("No confident acoustic match found.")
        if results:
            b = results[0]
            print(f"(best guess: {b.media.label()} @ {b.confidence:.1%} "
                  f"- below threshold {threshold:.0%})")
        print("=" * 60)
        return results
    print("ACOUSTIC MATCH RESULTS")
    print("-" * 60)
    for i, r in enumerate(results, 1):
        flag = ">>" if i == 1 else "  "
        loc = f"@{(r.ref_start_ms or 0)/1000:.0f}s" if r.ref_start_ms is not None else ""
        print(f"{flag} {i}. {r.media.label():38} {r.confidence:6.1%}  "
              f"({r.matched_frames}/{r.query_frames} frames {loc})")
    print("=" * 60)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create/identify acoustic (Chromaprint) fingerprints.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single audio/video file")
    group.add_argument("--dir", help="Directory of audio/video files")
    group.add_argument("--list", action="store_true",
                       help="List media with acoustic fingerprints")
    parser.add_argument("--identify", action="store_true",
                        help="Identify against DB instead of storing")
    parser.add_argument("--title")
    parser.add_argument("--year", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--episode", type=int)
    parser.add_argument("--type", choices=["tv", "movie"])
    parser.add_argument("--force", action="store_true",
                        help="Re-process files even if they already have acoustic "
                             "fingerprints. By default, files already in the "
                             "database are skipped automatically.")
    parser.add_argument("--config")
    parser.add_argument("--db")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    ac_cfg = af.AcousticConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    db = FingerprintDB(db_path)

    try:
        if args.list:
            rows = db.list_media()
            cur = db.conn.cursor()
            print(f"{'Title':28} {'Year':6} {'S':>3} {'E':>3} {'Segments':>9}")
            print("-" * 60)
            any_row = False
            for r in rows:
                cur.execute("SELECT COUNT(*) AS c FROM acoustic_segments WHERE media_id=?",
                            (r["id"],))
                segs = cur.fetchone()["c"]
                if segs == 0:
                    continue
                any_row = True
                print(f"{r['title'][:28]:28} {str(r['year'] or ''):6} "
                      f"{str(r['season'] or ''):>3} {str(r['episode'] or ''):>3} "
                      f"{segs:>9}")
            if not any_row:
                print("(no acoustic fingerprints yet)")
            print("\nStats:", db.stats())
            return 0

        if args.file:
            files = [args.file]
        else:
            files = sorted(
                f for f in glob.glob(os.path.join(args.dir, "**", "*"), recursive=True)
                if f.lower().endswith(MEDIA_EXTS)
            )
        if not files:
            logging.error("No audio/video files found.")
            return 1

        if args.identify:
            threshold = ac_cfg.confidence_threshold
            for f in files:
                identify_one(f, db, ac_cfg, threshold)
            return 0

        grand = processed = skipped = 0
        for i, f in enumerate(files, 1):
            already = af.file_already_acoustic_fingerprinted(db, f)
            if already and not args.force:
                logging.info("Skipping already processed file: %s", os.path.basename(f))
                skipped += 1
                continue
            if already and args.force:
                logging.info("Re-processing file: %s", os.path.basename(f))
            logging.info("[%d/%d] %s", i, len(files), os.path.basename(f))
            grand += fingerprint_one(f, db, ac_cfg, args.title, args.year,
                                     args.season, args.episode, args.type)
            processed += 1
        print("\n" + "=" * 56)
        print(f"Done. Stored acoustic fingerprints for {grand} segment(s).")
        print(f"Processed {processed} new files, skipped {skipped} existing files")
        print("Database stats:", db.stats())
        print(f"Database: {db_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
