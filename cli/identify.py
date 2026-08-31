#!/usr/bin/env python3
"""dvd-identify - batch-identify DVD-ripped episodes via dialogue matching.

This is a THIN command-line wrapper. It parses arguments, prepares the shared
speech-to-text engine and the reference-DB config, then hands the actual work to
``audio_fingerprint.engine.batch.batch_identify``. All matching, scoring and
output logic lives in the engine package so it can be tested and reused without
the CLI.

Examples
--------
    # identify every video in ./dvd_rips, write results next to them
    python -m audio_fingerprint.cli.identify --dir ./dvd_rips

    # a single file, more samples, custom output
    python -m audio_fingerprint.cli.identify --file "Disc1_Title3.mkv" \
        --samples 7 --csv out.csv --json out.json

    # use 8 parallel workers on a big folder
    python -m audio_fingerprint.cli.identify --dir ./rips --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure the flat engine modules on the project root are importable.
from . import _ROOT  # noqa: F401  (side effect: puts project root on sys.path)

from fingerprint_core import FingerprintConfig, load_config, setup_logging
from engine.types import DEFAULT_SAMPLE_POINTS
from engine.discovery import discover_media
from engine.batch import batch_identify, write_csv, write_json


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_points(spec: str):
    pts = []
    for tok in spec.split(","):
        tok = tok.strip().rstrip("%")
        if not tok:
            continue
        v = float(tok)
        if v > 1.0:
            v /= 100.0
        pts.append(max(0.0, min(1.0, v)))
    return pts or DEFAULT_SAMPLE_POINTS


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Batch-identify DVD-ripped episodes via multi-point "
                    "dialogue (phonetic) sampling.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="directory of video files to identify")
    src.add_argument("--file", help="a single video file to identify")

    ap.add_argument("--db", help="fingerprint DB path "
                    "(default: from config.json)")
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--samples", type=int, default=5,
                    help="number of sample points (ignored if --points given)")
    ap.add_argument("--points", type=parse_points, default=None,
                    help="comma-separated sample positions, e.g. "
                         "'10,30,50,70,90' or '0.1,0.5,0.9'")
    ap.add_argument("--sample-len", type=float, default=12.0,
                    help="length of each audio sample in seconds (default 12)")
    ap.add_argument("--workers", type=int, default=4,
                    help="number of files to identify in parallel (default 4)")
    ap.add_argument("--review-confidence", type=float, default=0.35,
                    help="flag for manual review below this confidence "
                         "(default 0.35)")
    ap.add_argument("--show-title", dest="show_title", default=None,
                    help="expected TV show for these files; candidates whose "
                         "stored show title matches get a confidence boost "
                         "(and thin cross-show matches are de-emphasised)")
    ap.add_argument("--runtimes", help="optional JSON mapping episode_id -> "
                    "expected runtime in minutes, for a sanity check")
    ap.add_argument("--runtime-tolerance", type=float, default=4.0,
                    help="minutes of runtime difference tolerated (default 4)")
    ap.add_argument("--csv", help="write results to this CSV file")
    ap.add_argument("--json", help="write results to this JSON file")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="per-sample logging")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging("INFO" if args.verbose else "WARNING")

    # sample points
    if args.points is None:
        n = max(1, args.samples)
        if n == 1:
            args.points = [0.5]
        else:
            args.points = [round((i + 1) / (n + 1), 4) for i in range(n)]
    else:
        args.samples = len(args.points)

    workers = max(1, args.workers)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.exists(db_path):
        print(f"ERROR: fingerprint DB not found: {db_path}", file=sys.stderr)
        print("Build one first from your subtitles, e.g.:\n"
              "  python -m audio_fingerprint.cli.build_fingerprints "
              "--dir /path/to/subs", file=sys.stderr)
        return 2
    fp_cfg = FingerprintConfig.from_config(cfg)

    runtimes = None
    if args.runtimes:
        with open(args.runtimes, encoding="utf-8") as fh:
            runtimes = json.load(fh)

    if args.file:
        media = [args.file]
    else:
        media = discover_media(args.dir)
    if not media:
        print("No media files found.", file=sys.stderr)
        return 1

    # One shared speech-to-text engine for every worker. Vosk's Model is safe to
    # share across threads (each transcription builds its own recogniser).
    transcriber = None
    try:
        import stt_utils
        transcriber = stt_utils.get_transcriber(cfg)
    except Exception as exc:
        print(f"ERROR: could not initialise STT engine: {exc}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("  DVD EPISODE IDENTIFICATION (dialogue / phonetic matching)")
    print("=" * 70)
    print(f"  reference DB : {db_path}")
    print(f"  files        : {len(media)}")
    print(f"  sample points: {', '.join(f'{p:.0%}' for p in args.points)} "
          f"({args.sample_len:.0f}s each)")
    print(f"  workers      : {workers}")

    for p in media:
        if not os.path.exists(p):
            print(f"\n>>> {os.path.basename(p)}\n    (file not found, skipped)")

    results = batch_identify(media, db_path, fp_cfg, cfg, args, transcriber,
                             runtimes=runtimes, workers=workers)

    # summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    ok = [r for r in results if not r.needs_review]
    review = [r for r in results if r.needs_review]
    correct = [r for r in ok if r.name_status == "correct"]
    rename = [r for r in ok if r.name_status == "rename"]
    print(f"  identified confidently : {len(ok)}/{len(results)}")
    print(f"    named correctly      : {len(correct)}")
    print(f"    need renaming        : {len(rename)}")
    for r in ok:
        g = r.to_row()
        tag = "OK " if r.name_status == "correct" else "REN"
        print(f"    {tag} {g['filename']:<45.45}  -> {g['episode_id']}  "
              f"({g['method']}, {g['confidence']:.0%})")
        if r.name_status == "rename":
            print(f"        should be: {g['suggested_filename']}")
    if review:
        print(f"\n  needs manual review    : {len(review)}")
        for r in review:
            g = r.to_row()
            print(f"    !  {g['filename']:<45.45}  -> {g['episode_id']}  "
                  f"{('('+g['notes']+')') if g['notes'] else ''}")

    if args.csv:
        write_csv(results, args.csv)
        print(f"\n  CSV  written: {args.csv}")
    if args.json:
        write_json(results, args.json)
        print(f"  JSON written: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
