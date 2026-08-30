#!/usr/bin/env python3
"""Batch-identify DVD-ripped episodes against a reference fingerprint DB.

This is the FOCUSED tool for the real use case: you have a folder of episode
video files ripped from DVDs (clean audio, but out of order, mislabelled, or
with extended cuts) and you want to know *which episode each file is* so you can
rename them for Plex.

It identifies each file purely by its DIALOGUE (phonetic matching):

    MULTI-POINT DIALOGUE SAMPLING
      For every file it extracts a handful of short audio samples from spread
      out timestamps (default 10%, 30%, 50%, 70%, 90% of the runtime),
      transcribes each one with speech-to-text, concatenates the transcripts and
      matches the dialogue against the reference DB built from subtitles. Sampling
      several spread-out points makes the transcript representative of the whole
      episode while only decoding a few seconds of audio per file (not the whole
      file). Matching is exact phonetic-shingle first, with an order-preserving
      fuzzy fallback that tolerates speech-to-text word errors.

    RUNTIME SANITY CHECK (optional, secondary)
      If you supply expected episode runtimes (``--runtimes runtimes.json``) the
      tool flags any match whose file duration differs from the expected episode
      runtime by more than a tolerance - a cheap way to catch extended cuts or a
      confidently-wrong match.

    PARALLEL PROCESSING
      Files are identified concurrently with a thread pool (``--workers``,
      default 4). Each worker opens its own database connection and shares a
      single speech-to-text engine, so a folder of rips is processed several
      times faster on multi-core machines.

Why dialogue matching (researched for this use case):
  * Acoustic fingerprinting (Chromaprint) proved unreliable on DVD rips: it
    produced very low-confidence, frequently wrong matches, because the DVD audio
    encode differs enough from the reference to defeat acoustic hashing.
  * OCR on title cards  - most episodic TV (incl. Matlock) never shows the
    episode *title* on screen, so title-card OCR yields nothing useful.
  * Runtime-only matching - too coarse (many episodes share a runtime); good only
    as a secondary sanity check, which is exactly how it is used here.

Output: a mapping of ``filename -> episode id`` with confidence scores, written
as CSV and/or JSON, plus a console summary that highlights any low-confidence
files that need manual review.

Examples
--------
    # identify every video in ./dvd_rips, write results next to them
    python identify_dvd_episodes.py --dir ./dvd_rips

    # a single file, more samples, custom output
    python identify_dvd_episodes.py --file "Disc1_Title3.mkv" --samples 7 \
        --csv out.csv --json out.json

    # use 8 parallel workers on a big folder
    python identify_dvd_episodes.py --dir ./rips --workers 8
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fingerprint_core import (
    FingerprintDB, FingerprintConfig, MediaInfo,
    FuzzyConfig, load_config, setup_logging, fingerprint_text, score_matches,
    phonetic_token_stream, score_fuzzy_matches,
)

# Video/audio containers we will try to identify.
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg",
              ".ts", ".wmv", ".flv", ".webm"}
# Plain audio files are accepted too (handy for testing / audio-only rips).
AUDIO_EXTS = {".m4a", ".wav", ".mp3", ".flac", ".aac", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

DEFAULT_SAMPLE_POINTS = [0.10, 0.30, 0.50, 0.70, 0.90]

# Serialises console prints from parallel workers so lines do not interleave.
_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg)


# ---------------------------------------------------------------------------
# Subprocess helpers (ffmpeg / ffprobe) - no console windows on Windows
# ---------------------------------------------------------------------------
def _subprocess_flags() -> dict:
    """Extra kwargs for subprocess calls so ffmpeg/ffprobe never flash a console
    window on Windows. A no-op on other platforms."""
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _probe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **_subprocess_flags(),
        )
        return float(out.stdout.decode("utf-8", "ignore").strip())
    except (ValueError, OSError):
        return 0.0


def _ffmpeg_extract(path: str, start_s: float, length_s: float,
                    out_wav: str, sample_rate: int = 16000) -> bool:
    """Extract a mono 16-bit PCM window to ``out_wav`` via ffmpeg.

    Returns True on success. Only a few seconds of audio are decoded per call
    (via ``-ss``/``-t``), so whole files are never fully loaded.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", f"{start_s:.3f}",
             "-t", f"{length_s:.3f}", "-i", path,
             "-ac", "1", "-ar", str(sample_rate), "-sample_fmt", "s16",
             "-vn", out_wav],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **_subprocess_flags(),
        )
        return proc.returncode == 0 and os.path.exists(out_wav) \
            and os.path.getsize(out_wav) > 0
    except OSError as exc:
        logging.debug("  ffmpeg extract failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------
@dataclass
class EpisodeGuess:
    """One candidate episode identity with the evidence behind it."""
    episode_id: str                 # e.g. "S01E04"  (or "movie" / "?")
    title: str
    season: Optional[int]
    episode: Optional[int]
    votes: int                      # samples that contributed (kept for compat)
    total_samples: int              # samples that produced any transcript
    mean_confidence: float          # phonetic match confidence
    method: str = "phonetic"        # phonetic | fuzzy


@dataclass
class FileResult:
    filename: str
    path: str
    duration_s: float
    guess: Optional[EpisodeGuess]
    needs_review: bool
    notes: str = ""
    elapsed_s: float = 0.0

    def to_row(self) -> dict:
        g = self.guess
        return {
            "filename": self.filename,
            "episode_id": g.episode_id if g else "UNKNOWN",
            "title": g.title if g else "",
            "confidence": round(g.mean_confidence, 4) if g else 0.0,
            "agreement": (f"{g.votes}/{g.total_samples}" if g else "0/0"),
            "method": g.method if g else "none",
            "duration_s": round(self.duration_s, 1),
            "needs_review": self.needs_review,
            "notes": self.notes,
            "elapsed_s": round(self.elapsed_s, 2),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def episode_id_str(season: Optional[int], episode: Optional[int]) -> str:
    if season is not None and episode is not None:
        return f"S{season:02d}E{episode:02d}"
    if episode is not None:
        return f"E{episode:02d}"
    return "movie"


def discover_media(path_dir: str) -> List[str]:
    files = []
    for name in sorted(os.listdir(path_dir)):
        full = os.path.join(path_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in MEDIA_EXTS:
            files.append(full)
    return files


def sample_windows(duration: float, points: List[float],
                   sample_len: float) -> List[Tuple[float, float]]:
    """Turn fractional sample points into concrete (start_s, len_s) windows,
    clamped so every window stays inside the media."""
    windows: List[Tuple[float, float]] = []
    if duration <= 0:
        return [(0.0, sample_len)]
    for p in points:
        start = max(0.0, min(duration - sample_len, duration * p))
        if start < 0:
            start = 0.0
        length = min(sample_len, max(1.0, duration - start))
        if length < 1.0:
            continue
        windows.append((round(start, 2), round(length, 2)))
    # de-duplicate windows that collapsed onto each other on very short files
    uniq: List[Tuple[float, float]] = []
    for w in windows:
        if w not in uniq:
            uniq.append(w)
    return uniq or [(0.0, min(sample_len, max(1.0, duration)))]


# ---------------------------------------------------------------------------
# Phonetic fuzzy fallback (self-contained; tolerant of STT word errors)
# ---------------------------------------------------------------------------
def _load_candidate_streams(db: FingerprintDB, media_ids, fp_cfg):
    """Load ``media_id -> (MediaInfo, ref_tokens, ref_starts)`` for the fuzzy
    matcher. Only media rows that actually have a stored token stream are
    returned."""
    streams = {}
    for mid in media_ids:
        toks, starts = db.get_token_stream(mid)
        if not toks:
            continue
        info = db.media_info(mid)
        if info is None:
            continue
        streams[mid] = (info, toks, starts)
    return streams


def run_fuzzy_stage(query_text: str, db: FingerprintDB,
                    fp_cfg: FingerprintConfig, cfg: dict, candidate_ids):
    """Order-preserving phonetic LCS matching as a fallback when exact
    shingle-hash matching is weak (STT word errors).

    Searches the supplied ``candidate_ids`` first (if any) and widens to every
    media with a token stream if those yield nothing. Returns a list of
    MatchResult (possibly empty) and the FuzzyConfig that was used.
    """
    fuzzy_cfg = FuzzyConfig.from_config(cfg)
    if not fuzzy_cfg.enabled or not query_text.strip():
        return [], fuzzy_cfg
    q_tokens = phonetic_token_stream(query_text, fp_cfg)
    if len(q_tokens) < fuzzy_cfg.min_query_tokens:
        logging.info("  fuzzy: query too short (%d < %d tokens), skipping",
                     len(q_tokens), fuzzy_cfg.min_query_tokens)
        return [], fuzzy_cfg

    top_k = cfg.get("matching", {}).get("top_n_results", 5)
    scope = list(candidate_ids) if candidate_ids else []
    streams = _load_candidate_streams(db, scope, fp_cfg) if scope else {}
    if not streams:
        # widen to the whole token-stream corpus
        streams = _load_candidate_streams(
            db, db.all_token_stream_media_ids(), fp_cfg)
    if not streams:
        return [], fuzzy_cfg
    results = score_fuzzy_matches(q_tokens, streams, fuzzy_cfg, top_n=top_k)
    # only keep results that clear the configured LCS ratio
    results = [r for r in results if r.confidence >= fuzzy_cfg.min_lcs_ratio]
    if not results:
        return [], fuzzy_cfg

    # Margin gate: order-preserving LCS is biased toward longer / common-word
    # references, so a short noisy query can leave the top two candidates almost
    # tied (e.g. 0.87 vs 0.81). Only trust the winner when it clearly beats the
    # runner-up; otherwise the match is ambiguous and we return nothing.
    if len(results) >= 2:
        margin = results[0].confidence - results[1].confidence
        if margin < fuzzy_cfg.min_margin:
            logging.info("  fuzzy: ambiguous (%.0f%% vs %.0f%%, margin %.0f%% < "
                         "%.0f%%) - rejecting", results[0].confidence * 100,
                         results[1].confidence * 100, margin * 100,
                         fuzzy_cfg.min_margin * 100)
            return [], fuzzy_cfg
    return results, fuzzy_cfg


# ---------------------------------------------------------------------------
# Transcription of the sampled windows
# ---------------------------------------------------------------------------
def transcribe_samples(path: str, windows: List[Tuple[float, float]],
                       transcriber, sample_rate: int) -> Tuple[str, int]:
    """Extract each sample window with ffmpeg, transcribe it, and return the
    concatenated transcript plus the number of windows that yielded speech."""
    import stt_utils

    texts: List[str] = []
    got = 0
    tmpdir = tempfile.mkdtemp(prefix="dvdid_")
    try:
        for i, (start_s, length_s) in enumerate(windows):
            wav = os.path.join(tmpdir, f"s{i}.wav")
            if not _ffmpeg_extract(path, start_s, length_s, wav, sample_rate):
                logging.debug("  sample %d extract failed @%.1fs", i, start_s)
                continue
            try:
                seg = stt_utils.AudioSegment.from_file(wav)
                t = transcriber.transcribe_segment(seg)
            except Exception as exc:  # pragma: no cover - defensive
                logging.debug("  sample %d transcribe failed: %s", i, exc)
                t = ""
            finally:
                if os.path.exists(wav):
                    os.remove(wav)
            if t:
                texts.append(t)
                got += 1
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return " ".join(texts).strip(), got


# ---------------------------------------------------------------------------
# Orchestration for one file
# ---------------------------------------------------------------------------
def identify_one(path: str, db_path: str, fp_cfg: FingerprintConfig,
                 cfg: dict, args, transcriber,
                 runtimes: Optional[dict]) -> FileResult:
    """Identify a single file by dialogue. Opens its own DB connection so it is
    safe to run in a worker thread; the ``transcriber`` is shared (each
    transcription builds its own recogniser internally)."""
    fname = os.path.basename(path)
    t0 = time.time()
    _log(f"\n>>> {fname}")

    db = FingerprintDB(db_path)
    try:
        duration = _probe_duration(path)
        windows = sample_windows(duration, args.points, args.sample_len)

        sr = cfg.get("audio", {}).get("sample_rate", 16000)
        best: Optional[EpisodeGuess] = None
        notes_parts: List[str] = []

        if transcriber is None:
            notes_parts.append("STT engine unavailable")
            text, got = "", 0
        else:
            text, got = transcribe_samples(path, windows, transcriber, sr)

        if text:
            logging.info("  transcript (%d chars, %d/%d samples): \"%s%s\"",
                         len(text), got, len(windows), text[:80],
                         "..." if len(text) > 80 else "")
            # Stage 1: exact phonetic shingle match against the whole DB.
            query_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
            rows = db.lookup(query_hashes)
            results = score_matches(query_hashes, rows, cfg.get("matching", {}))
            if results:
                m = results[0].media
                best = EpisodeGuess(
                    episode_id=episode_id_str(m.season, m.episode),
                    title=m.title, season=m.season, episode=m.episode,
                    votes=got, total_samples=len(windows),
                    mean_confidence=results[0].confidence, method="phonetic",
                )
            else:
                # Stage 2: order-preserving fuzzy fallback (tolerates STT errors)
                fuzzy_results, _fc = run_fuzzy_stage(text, db, fp_cfg, cfg, [])
                if fuzzy_results:
                    m = fuzzy_results[0].media
                    best = EpisodeGuess(
                        episode_id=episode_id_str(m.season, m.episode),
                        title=m.title, season=m.season, episode=m.episode,
                        votes=got, total_samples=len(windows),
                        mean_confidence=fuzzy_results[0].confidence,
                        method="fuzzy",
                    )
        elif transcriber is not None:
            notes_parts.append("no speech recognised")

        # Build review flag
        review_conf = args.review_confidence
        needs_review = False
        if best is None:
            needs_review = True
            if not notes_parts:
                notes_parts.append("no match")
        else:
            if best.mean_confidence < review_conf:
                needs_review = True
                notes_parts.append(f"low confidence {best.mean_confidence:.0%}")

        # Optional runtime sanity check
        if best is not None and runtimes:
            exp = runtimes.get(best.episode_id) or runtimes.get(
                best.episode_id.lower())
            if exp:
                diff = abs(duration / 60.0 - float(exp))
                if diff > args.runtime_tolerance:
                    needs_review = True
                    notes_parts.append(
                        f"runtime {duration/60:.0f}m vs expected {exp}m")

        result = FileResult(
            filename=fname, path=path, duration_s=duration, guess=best,
            needs_review=needs_review, notes="; ".join(notes_parts),
            elapsed_s=time.time() - t0,
        )

        if best is not None:
            flag = "  ! REVIEW" if needs_review else "  OK"
            _log(f"    => {best.episode_id}  {best.title}  "
                 f"[{best.method}, conf {best.mean_confidence:.0%}]{flag}")
            if result.notes:
                _log(f"       note: {result.notes}")
        else:
            _log(f"    => UNKNOWN  ! REVIEW ({result.notes or 'no match'})")
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def write_csv(results: List[FileResult], path: str) -> None:
    fields = ["filename", "episode_id", "title", "confidence", "agreement",
              "method", "duration_s", "needs_review", "notes", "elapsed_s"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(r.to_row())


def write_json(results: List[FileResult], path: str) -> None:
    payload = [r.to_row() for r in results]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_points(spec: str) -> List[float]:
    pts: List[float] = []
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
              "  python create_fingerprint.py --dir /path/to/subs", file=sys.stderr)
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

    results: List[FileResult] = []
    existing = [p for p in media if os.path.exists(p)]
    for p in media:
        if not os.path.exists(p):
            print(f"\n>>> {os.path.basename(p)}\n    (file not found, skipped)")

    if workers == 1:
        for path in existing:
            results.append(identify_one(path, db_path, fp_cfg, cfg, args,
                                        transcriber, runtimes))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(identify_one, path, db_path, fp_cfg, cfg, args,
                                transcriber, runtimes): path
                    for path in existing}
            for fut in as_completed(futs):
                results.append(fut.result())

    # keep output order stable (input order) regardless of completion order
    order = {p: i for i, p in enumerate(existing)}
    results.sort(key=lambda r: order.get(r.path, 0))

    # summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    ok = [r for r in results if not r.needs_review]
    review = [r for r in results if r.needs_review]
    print(f"  identified confidently : {len(ok)}/{len(results)}")
    for r in ok:
        g = r.to_row()
        print(f"    OK {g['filename']:<45.45}  -> {g['episode_id']}  "
              f"({g['method']}, {g['confidence']:.0%})")
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
