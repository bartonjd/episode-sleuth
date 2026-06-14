#!/usr/bin/env python3
"""
fingerprint_audio.py
====================
Fingerprint audio FILES on disk (mp3, wav, m4a, flac, ogg, mp4 ...).

The audio is transcribed with the configured STT engine, then run through the
exact same phonetic-shingle pipeline as the subtitle fingerprinter, so audio
fingerprints are directly comparable to subtitle fingerprints.

This is useful for batch-indexing media you already have, or for evaluating how
well STT output matches subtitle fingerprints.

Examples
--------
  # Add an episode's audio to the database
  python fingerprint_audio.py --file episode1.mp3 --title "Matlock" --year 1986 \
        --season 1 --episode 1

  # Batch fingerprint a folder
  python fingerprint_audio.py --dir /media/matlock --title "Matlock" --year 1986

  # Just transcribe + identify against the existing DB (don't store)
  python fingerprint_audio.py --file clip.wav --identify

  # Re-process files even if already in the database
  python fingerprint_audio.py --dir /media/matlock --force

Note
----
Files already fingerprinted (for the selected method) are skipped automatically,
matched by their source file path. Pass --force to re-process them anyway.
"""

import os
import sys
import glob
import argparse
import logging

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    MediaInfo, fingerprint_text, score_matches,
)
import subtitle_utils as su
import stt_utils

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mkv", ".webm")


def chunk_segment(seg, chunk_seconds, overlap_seconds):
    """Yield (start_ms, end_ms, AudioSegment) windows with overlap."""
    total = len(seg)
    step = max(1, (chunk_seconds - overlap_seconds)) * 1000
    win = chunk_seconds * 1000
    pos = 0
    while pos < total:
        end = min(pos + win, total)
        yield pos, end, seg[pos:end]
        if end >= total:
            break
        pos += step


def transcribe_file(path, transcriber, cfg):
    """Transcribe an audio file in overlapping chunks -> list of (start,end,text)."""
    audio_cfg = cfg.get("audio", {})
    sr = audio_cfg.get("sample_rate", 16000)
    seg = stt_utils.load_audio_mono16k(path, sr)
    chunk_s = audio_cfg.get("chunk_seconds", 8)
    overlap_s = audio_cfg.get("overlap_seconds", 2)

    results = []
    chunks = list(chunk_segment(seg, chunk_s, overlap_s))
    for i, (start_ms, end_ms, sub) in enumerate(chunks, 1):
        text = transcriber.transcribe_segment(sub)
        if text:
            results.append((start_ms, end_ms, text))
        logging.info("  transcribed chunk %d/%d (%.0fs) %s",
                     i, len(chunks), start_ms / 1000.0,
                     ("-> " + text[:50]) if text else "(silence)")
    return results


def fingerprint_audio_file(path, db, fp_cfg, transcriber, cfg,
                           title=None, year=None, season=None, episode=None,
                           media_type=None, reindex=True):
    logging.info("Transcribing %s ...", os.path.basename(path))
    cues = transcribe_file(path, transcriber, cfg)
    if not cues:
        logging.warning("No speech recognised in %s", path)
        return 0

    info = su.parse_filename_metadata(path, title, year)
    if season is not None:
        info.season = season
    if episode is not None:
        info.episode = episode
    if media_type:
        info.media_type = media_type

    if reindex:
        db.clear_media(info)
    media_id = db.get_or_create_media(info)

    rows = []
    for (start_ms, end_ms, text) in cues:
        for (h, size) in fingerprint_text(text, fp_cfg):
            rows.append((h, size, start_ms, end_ms))
    total = db.add_fingerprints(media_id, rows) if rows else 0
    logging.info("  + %-45s -> %5d fingerprints", info.label(), total)
    return total


def identify_audio_file(path, db, fp_cfg, transcriber, cfg):
    logging.info("Transcribing %s for identification ...", os.path.basename(path))
    cues = transcribe_file(path, transcriber, cfg)
    text = " ".join(t for (_a, _b, t) in cues)
    if not text.strip():
        print("No speech recognised; cannot identify.")
        return []
    query_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
    rows = db.lookup(query_hashes)
    results = score_matches(query_hashes, rows, cfg.get("matching", {}))
    print_results(results, cfg)
    return results


def print_results(results, cfg):
    threshold = cfg.get("matching", {}).get("confidence_threshold", 0.15)
    print("\n" + "=" * 60)
    if not results or results[0].confidence < threshold:
        print("No confident match found.")
        if results:
            best = results[0]
            print(f"(best guess: {best.media.label()} "
                  f"@ {best.confidence:.1%} - below threshold {threshold:.0%})")
        return
    print("MATCH RESULTS")
    print("-" * 60)
    for i, r in enumerate(results, 1):
        flag = ">>" if (i == 1 and r.confidence >= threshold) else "  "
        print(f"{flag} {i}. {r.media.label():40} {r.confidence:6.1%}  "
              f"({r.match_count} hits)")
    print("=" * 60)


def store_acoustic_file(path, db, ac_cfg, title=None, year=None,
                        season=None, episode=None, media_type=None):
    """Store acoustic (Chromaprint) fingerprints for one media file."""
    import acoustic_fingerprint as af
    info = su.parse_filename_metadata(path, title, year)
    if season is not None:
        info.season = season
    if episode is not None:
        info.episode = episode
    if media_type:
        info.media_type = media_type
    return af.store_acoustic_fingerprints(db, info, path, ac_cfg, reindex=True)


def identify_combined(path, db, fp_cfg, ac_cfg, transcriber, cfg,
                      use_phonetic, use_acoustic):
    """Run phonetic and/or acoustic identification and report the best method."""
    import acoustic_fingerprint as af

    phon_best = None
    ac_best = None

    if use_phonetic and transcriber is not None:
        logging.info("Transcribing %s for phonetic identification ...",
                     os.path.basename(path))
        cues = transcribe_file(path, transcriber, cfg)
        text = " ".join(t for (_a, _b, t) in cues)
        if text.strip():
            query_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
            rows = db.lookup(query_hashes)
            phon_results = score_matches(query_hashes, rows, cfg.get("matching", {}))
            if phon_results:
                phon_best = phon_results[0]
        else:
            logging.info("No speech recognised for phonetic matching.")

    if use_acoustic:
        logging.info("Acoustic fingerprinting %s ...", os.path.basename(path))
        ac_results = af.identify_file_acoustic(path, db, ac_cfg)
        if ac_results:
            ac_best = ac_results[0]

    print_combined_result(phon_best, ac_best, cfg, ac_cfg)
    return phon_best, ac_best


def print_combined_result(phon_best, ac_best, cfg, ac_cfg):
    phon_thr = cfg.get("matching", {}).get("confidence_threshold", 0.15)
    ac_thr = ac_cfg.confidence_threshold
    print("\n" + "=" * 60)
    print("HYBRID IDENTIFICATION (phonetic + acoustic)")
    print("-" * 60)
    if phon_best:
        ok = "OK " if phon_best.confidence >= phon_thr else "low"
        print(f"  phonetic : {phon_best.media.label():38} "
              f"{phon_best.confidence:6.1%} [{ok}]")
    else:
        print("  phonetic : (no match)")
    if ac_best:
        ok = "OK " if ac_best.confidence >= ac_thr else "low"
        loc = (f" @{(ac_best.ref_start_ms or 0)/1000:.0f}s"
               if ac_best.ref_start_ms is not None else "")
        print(f"  acoustic : {ac_best.media.label():38} "
              f"{ac_best.confidence:6.1%} [{ok}]{loc}")
    else:
        print("  acoustic : (no match)")

    # Choose the winner: prefer a method that clears its own threshold; if both
    # do (or neither), prefer the higher confidence.
    candidates = []
    if phon_best:
        candidates.append(("phonetic", phon_best, phon_best.confidence,
                           phon_best.confidence >= phon_thr))
    if ac_best:
        candidates.append(("acoustic", ac_best, ac_best.confidence,
                           ac_best.confidence >= ac_thr))
    print("-" * 60)
    if not candidates:
        print(">>> No match found by either method.")
        print("=" * 60)
        return None
    passing = [c for c in candidates if c[3]]
    pool = passing if passing else candidates
    method, best, conf, _ok = max(pool, key=lambda c: c[2])
    if passing:
        print(f">>> IDENTIFIED via {method.upper()}: {best.media.label()} "
              f"({conf:.1%})")
    else:
        print(f">>> Best guess via {method.upper()}: {best.media.label()} "
              f"({conf:.1%}) - below threshold")
    print("=" * 60)
    return method, best


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fingerprint or identify audio files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single audio file")
    group.add_argument("--dir", help="Directory of audio files")
    parser.add_argument("--identify", action="store_true",
                        help="Identify against DB instead of storing")
    parser.add_argument("--acoustic", action="store_true",
                        help="Use ACOUSTIC (Chromaprint) fingerprints only "
                             "(sound-based; no transcription)")
    parser.add_argument("--both", action="store_true",
                        help="Use BOTH phonetic (dialogue) and acoustic (sound) "
                             "fingerprints")
    parser.add_argument("--title")
    parser.add_argument("--year", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--episode", type=int)
    parser.add_argument("--type", choices=["tv", "movie"])
    parser.add_argument("--force", action="store_true",
                        help="Re-process files even if they are already in the "
                             "database. By default, files already fingerprinted "
                             "(for the selected method) are skipped automatically.")
    parser.add_argument("--config")
    parser.add_argument("--db")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)

    # Decide which method(s) to use. Default is phonetic only.
    use_acoustic = args.acoustic or args.both
    use_phonetic = args.both or not args.acoustic

    import acoustic_fingerprint as af
    ac_cfg = af.AcousticConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    db = FingerprintDB(db_path)

    # STT is only needed for the phonetic method.
    transcriber = None
    if use_phonetic:
        try:
            transcriber = stt_utils.get_transcriber(cfg)
        except Exception as exc:
            logging.error("Could not initialise STT engine: %s", exc)
            db.close()
            return 2

    try:
        if args.file:
            files = [args.file]
        else:
            files = sorted(
                f for f in glob.glob(os.path.join(args.dir, "**", "*"), recursive=True)
                if f.lower().endswith(AUDIO_EXTS)
            )
        if not files:
            logging.error("No audio files found.")
            return 1

        if args.identify:
            for f in files:
                if use_phonetic and use_acoustic:
                    identify_combined(f, db, fp_cfg, ac_cfg, transcriber, cfg,
                                      use_phonetic, use_acoustic)
                elif use_acoustic:
                    identify_combined(f, db, fp_cfg, ac_cfg, None, cfg,
                                      False, True)
                else:
                    identify_audio_file(f, db, fp_cfg, transcriber, cfg)
        else:
            grand = 0
            ac_total = 0
            processed = skipped = 0
            for i, f in enumerate(files, 1):
                # A file counts as "already processed" only when every selected
                # method already has fingerprints for it.
                done_phon = db.file_has_phonetic(f) if use_phonetic else True
                done_ac = db.file_has_acoustic(f) if use_acoustic else True
                already = done_phon and done_ac
                if already and not args.force:
                    logging.info("Skipping already processed file: %s",
                                 os.path.basename(f))
                    skipped += 1
                    continue
                if already and args.force:
                    logging.info("Re-processing file: %s", os.path.basename(f))
                logging.info("[%d/%d] %s", i, len(files), os.path.basename(f))
                if use_phonetic:
                    grand += fingerprint_audio_file(
                        f, db, fp_cfg, transcriber, cfg,
                        args.title, args.year, args.season, args.episode, args.type)
                if use_acoustic:
                    ac_total += store_acoustic_file(
                        f, db, ac_cfg,
                        args.title, args.year, args.season, args.episode, args.type)
                processed += 1
            msg = []
            if use_phonetic:
                msg.append(f"{grand} phonetic fingerprints")
            if use_acoustic:
                msg.append(f"{ac_total} acoustic segments")
            print("\nDone. Added %s. Stats: %s" % (" + ".join(msg), db.stats()))
            print(f"Processed {processed} new files, skipped {skipped} existing files")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
