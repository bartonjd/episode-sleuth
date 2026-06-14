#!/usr/bin/env python3
"""
fingerprint_audio.py
====================
Build ACOUSTIC (Chromaprint) fingerprints for audio/video FILES on disk
(mp3, wav, m4a, flac, ogg, mp4, mkv, webm ...).

This script is sound-based only: it runs the audio waveform through Chromaprint
(`fpcalc`) and stores the resulting acoustic fingerprints. It does NOT transcribe
anything.

Why no transcription?
---------------------
The reference database keeps two complementary kinds of fingerprints:

  * phonetic (dialogue)  -> built from SUBTITLES via create_fingerprint.py
  * acoustic (sound)     -> built from AUDIO via this script / create_acoustic_fingerprint.py

Because subtitles already give us accurate dialogue, there is no reason to
transcribe reference media when building the database. Transcription is only
needed when identifying UNKNOWN audio, which is handled by identify_audio.py.

Examples
--------
  # Add an episode's audio to the acoustic database
  python fingerprint_audio.py --file episode1.mp3 --title "Matlock" --year 1986 \
        --season 1 --episode 1

  # Batch fingerprint a folder
  python fingerprint_audio.py --dir /media/matlock --title "Matlock" --year 1986

  # Re-process files even if already in the database
  python fingerprint_audio.py --dir /media/matlock --force

Note
----
Files that already have acoustic fingerprints are skipped automatically, matched
by their source file path. Pass --force to re-process them anyway.

(For acoustic identification of an unknown clip, use identify_audio.py --acoustic
or create_acoustic_fingerprint.py --identify.)
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

MEDIA_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
              ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v")


def store_acoustic_file(path, db, ac_cfg, title=None, year=None,
                        season=None, episode=None, media_type=None):
    """Store acoustic (Chromaprint) fingerprints for one media file."""
    info = su.parse_filename_metadata(path, title, year)
    if season is not None:
        info.season = season
    if episode is not None:
        info.episode = episode
    if media_type:
        info.media_type = media_type
    return af.store_acoustic_fingerprints(db, info, path, ac_cfg, reindex=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build ACOUSTIC (Chromaprint) fingerprints from audio/video files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single audio/video file")
    group.add_argument("--dir", help="Directory of audio/video files")
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

        grand = processed = skipped = 0
        for i, f in enumerate(files, 1):
            already = af.file_already_acoustic_fingerprinted(db, f)
            if already and not args.force:
                logging.info("Skipping already processed file: %s",
                             os.path.basename(f))
                skipped += 1
                continue
            if already and args.force:
                logging.info("Re-processing file: %s", os.path.basename(f))
            logging.info("[%d/%d] %s", i, len(files), os.path.basename(f))
            grand += store_acoustic_file(
                f, db, ac_cfg,
                args.title, args.year, args.season, args.episode, args.type)
            processed += 1

        print("\n" + "=" * 56)
        print(f"Done. Added {grand} acoustic segments. Stats: {db.stats()}")
        print(f"Processed {processed} new files, skipped {skipped} existing files")
        print(f"Database: {db_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
