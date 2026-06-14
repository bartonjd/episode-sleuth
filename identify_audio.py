#!/usr/bin/env python3
"""
identify_audio.py
=================
Listen to the microphone in real time and identify which TV show / movie is
playing, using the phonetic fingerprint database built by create_fingerprint.py.

How it works
------------
  * Audio is captured continuously from the default microphone.
  * A sliding window of the most recent N seconds is transcribed with STT.
  * The transcript is converted to phonetic shingles -> hashes (same pipeline
    as the subtitles) and looked up in the database.
  * A rolling buffer of recent shingles is scored so confidence builds up over
    time. Results are printed whenever they exceed the configured threshold.

Examples
--------
  python identify_audio.py                 # live microphone
  python identify_audio.py --seconds 60    # stop after 60 s
  python identify_audio.py --once          # one window, print best guess, exit

Requires PyAudio for live capture:
  pip install pyaudio   (plus system 'portaudio19-dev' on Debian/Ubuntu)
"""

import os
import sys
import time
import queue
import argparse
import logging
import collections

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    fingerprint_text, score_matches,
)
import stt_utils


def open_microphone(sample_rate):
    """Return (pyaudio_instance, stream, frames_per_buffer)."""
    try:
        import pyaudio
    except ImportError:
        raise RuntimeError(
            "PyAudio is required for live capture. Install with:\n"
            "  sudo apt-get install portaudio19-dev && pip install pyaudio"
        )
    pa = pyaudio.PyAudio()
    frames_per_buffer = 4000
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=sample_rate,
        input=True,
        frames_per_buffer=frames_per_buffer,
    )
    return pa, stream, frames_per_buffer


def pcm_to_segment(pcm_bytes, sample_rate):
    from pydub import AudioSegment
    return AudioSegment(data=pcm_bytes, sample_width=2, frame_rate=sample_rate, channels=1)


def run_live(db, fp_cfg, transcriber, cfg, max_seconds=None, once=False):
    audio_cfg = cfg.get("audio", {})
    match_cfg = cfg.get("matching", {})
    sr = audio_cfg.get("sample_rate", 16000)
    window_s = audio_cfg.get("chunk_seconds", 8)
    overlap_s = audio_cfg.get("overlap_seconds", 2)
    threshold = match_cfg.get("confidence_threshold", 0.15)
    min_matches = match_cfg.get("min_matches", 3)

    pa, stream, fpb = open_microphone(sr)
    logging.info("Listening on microphone (%d Hz). Press Ctrl+C to stop.", sr)

    window_bytes = window_s * sr * 2          # 16-bit
    step_bytes = max(1, (window_s - overlap_s)) * sr * 2
    buf = bytearray()
    # rolling buffer of recent query hashes across windows (time-windowed)
    recent_hashes = collections.deque(maxlen=2000)

    start_time = time.time()
    try:
        while True:
            data = stream.read(fpb, exception_on_overflow=False)
            buf.extend(data)

            if len(buf) >= window_bytes:
                seg = pcm_to_segment(bytes(buf[-window_bytes:]), sr)
                text = transcriber.transcribe_segment(seg)
                # slide window forward
                del buf[:step_bytes]

                if text:
                    logging.info("heard: %s", text[:70])
                    new_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
                    recent_hashes.extend(new_hashes)

                    query = list(recent_hashes)
                    rows = db.lookup(query)
                    results = score_matches(query, rows, match_cfg)
                    if results and results[0].confidence >= threshold \
                            and results[0].match_count >= min_matches:
                        print_live_result(results, threshold)
                        if once:
                            break
                elif once:
                    logging.info("(no speech detected in window)")

            if max_seconds and (time.time() - start_time) > max_seconds:
                logging.info("Reached time limit (%ss).", max_seconds)
                break
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def print_live_result(results, threshold):
    best = results[0]
    print("\n" + "=" * 60)
    print(f">>> IDENTIFIED: {best.media.label()}")
    print(f"    confidence : {best.confidence:.1%}  ({best.match_count} hash hits)")
    if best.window_start_ms is not None:
        print(f"    approx time: {best.window_start_ms/1000:.0f}s into source")
    if len(results) > 1:
        runners = ", ".join(f"{r.media.label()} ({r.confidence:.0%})"
                            for r in results[1:3])
        print(f"    runners-up : {runners}")
    print("=" * 60 + "\n")


def run_file_fallback(path, db, fp_cfg, transcriber, cfg):
    """Identify from a recorded file (useful when no mic is available)."""
    import fingerprint_audio
    fingerprint_audio.identify_audio_file(path, db, fp_cfg, transcriber, cfg)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Identify live audio against the fingerprint DB.")
    parser.add_argument("--seconds", type=int, help="Stop after N seconds")
    parser.add_argument("--once", action="store_true",
                        help="Stop after the first confident match")
    parser.add_argument("--from-file",
                        help="Identify from a recorded audio file instead of the mic")
    parser.add_argument("--config")
    parser.add_argument("--db")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    db = FingerprintDB(db_path)

    if db.stats()["fingerprints"] == 0:
        logging.error("Fingerprint database is empty. Run create_fingerprint.py first.")
        db.close()
        return 1

    try:
        transcriber = stt_utils.get_transcriber(cfg)
    except Exception as exc:
        logging.error("Could not initialise STT engine: %s", exc)
        db.close()
        return 2

    try:
        if args.from_file:
            run_file_fallback(args.from_file, db, fp_cfg, transcriber, cfg)
        else:
            run_live(db, fp_cfg, transcriber, cfg,
                     max_seconds=args.seconds, once=args.once)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
