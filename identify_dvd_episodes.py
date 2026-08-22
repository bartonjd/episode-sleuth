#!/usr/bin/env python3
"""Batch-identify DVD-ripped episodes against a reference fingerprint DB.

This is the FOCUSED tool for the real use case: you have a folder of episode
video files ripped from DVDs (clean audio, but out of order, mislabelled, or
with extended cuts) and you want to know *which episode each file is* so you can
rename them for Plex.

It is deliberately simpler than the general ``identify_audio.py`` Shazam-style
tool. It does NOT do live microphone capture or full-file transcription. Instead
it uses the strategy that is fastest and most accurate for CLEAN video files:

    MULTI-POINT ACOUSTIC SAMPLING (primary)
      For every video it extracts a handful of short audio samples from spread
      out timestamps (default 10%, 30%, 50%, 70%, 90% of the runtime), acoustic-
      fingerprints each one, and matches them against the reference DB. Because
      DVD audio is a near-clean copy of the broadcast/subtitle-timed audio, the
      acoustic match is fast and precise. Sampling several points and taking a
      *vote* makes the result robust to ad-break black frames, recap intros,
      "previously on" segments and extended-cut inserts that would fool a single
      sample.

    SCOPED PHONETIC FALLBACK (only when acoustic is weak / ambiguous)
      If the acoustic vote is low-confidence or the samples disagree, the tool
      transcribes the same samples and runs phonetic (dialogue) matching scoped
      to the top acoustic candidates, with an order-preserving fuzzy fallback.
      This rescues files whose audio was re-encoded aggressively.

    RUNTIME SANITY CHECK (optional, secondary)
      If you supply expected episode runtimes (``--runtimes runtimes.json``) the
      tool flags any match whose file duration differs from the expected episode
      runtime by more than a tolerance - a cheap way to catch extended cuts or a
      confidently-wrong match.

Why NOT the other approaches (researched for this use case):
  * OCR on title cards  - most episodic TV (incl. Matlock) never shows the
    episode *title* on screen, so title-card OCR yields nothing useful.
  * Visual/frame fingerprinting - heavier (frame extraction + perceptual hashing)
    and needs a visual reference we do not have; the audio is already a strong,
    cheap signal for clean rips.
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

    # acoustic only (skip the phonetic fallback entirely - fastest)
    python identify_dvd_episodes.py --dir ./rips --no-phonetic
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import acoustic_fingerprint as af
from acoustic_fingerprint import AcousticConfig, FpcalcNotFoundError
from fingerprint_core import (
    FingerprintDB, FingerprintConfig, MediaInfo,
    load_config, setup_logging, fingerprint_text, score_matches,
)

# Video/audio containers we will try to identify.
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg",
              ".ts", ".wmv", ".flv", ".webm"}
# Plain audio files are accepted too (handy for testing / audio-only rips).
AUDIO_EXTS = {".m4a", ".wav", ".mp3", ".flac", ".aac", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

DEFAULT_SAMPLE_POINTS = [0.10, 0.30, 0.50, 0.70, 0.90]


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
    votes: int                      # how many samples ranked it #1
    total_samples: int              # samples that produced any match
    mean_confidence: float          # mean acoustic confidence of the winning samples
    method: str = "acoustic"        # acoustic | phonetic | fuzzy


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
def episode_key(title: str, season: Optional[int],
                episode: Optional[int]) -> Tuple[str, Optional[int], Optional[int]]:
    return (title, season, episode)


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
# Stage 1: multi-point acoustic identification
# ---------------------------------------------------------------------------
def acoustic_identify(path: str, db: FingerprintDB, ac_cfg: AcousticConfig,
                      points: List[float], sample_len: float,
                      top_n: int = 5) -> Tuple[float, List[EpisodeGuess],
                                               Dict[int, EpisodeGuess]]:
    """Sample the file at several points, acoustic-match each, and vote.

    Returns (duration, ranked_guesses, best_acoustic_by_media_id).
    """
    duration = af._probe_duration(path)
    windows = sample_windows(duration, points, sample_len)

    # top-1 vote tally (drives the final acoustic verdict)
    votes: Dict[Tuple, int] = {}
    conf_sum: Dict[Tuple, float] = {}
    # candidate pool = every episode seen in ANY sample's top-N (drives the
    # phonetic-fallback scope so it is not poisoned by a single wrong top-1)
    best_conf: Dict[Tuple, float] = {}
    meta: Dict[Tuple, MediaInfo] = {}
    samples_with_match = 0

    tmpdir = tempfile.mkdtemp(prefix="dvdid_")
    try:
        for i, (start_s, length_s) in enumerate(windows):
            wav = os.path.join(tmpdir, f"s{i}.wav")
            if not af._ffmpeg_extract(path, start_s, length_s, wav):
                logging.debug("  sample %d extract failed @%.1fs", i, start_s)
                continue
            try:
                _dur, raw = af.generate_fingerprint(wav, ac_cfg.fpcalc_path)
            except FpcalcNotFoundError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logging.debug("  sample %d fingerprint failed: %s", i, exc)
                continue
            finally:
                if os.path.exists(wav):
                    os.remove(wav)
            if not raw:
                continue
            results = af.match_acoustic(raw, db, ac_cfg, top_n=top_n)
            if not results:
                logging.info("  sample %d @%5.1fs -> no acoustic match",
                             i + 1, start_s)
                continue
            samples_with_match += 1
            # every result feeds the candidate pool ...
            for res in results:
                k = episode_key(res.media.title, res.media.season,
                                res.media.episode)
                best_conf[k] = max(best_conf.get(k, 0.0), res.confidence)
                meta[k] = res.media
            # ... but only the top-1 casts a vote for the final verdict
            top = results[0]
            key = episode_key(top.media.title, top.media.season,
                              top.media.episode)
            votes[key] = votes.get(key, 0) + 1
            conf_sum[key] = conf_sum.get(key, 0.0) + top.confidence
            logging.info("  sample %d @%5.1fs -> %s (%.0f%%)", i + 1, start_s,
                         top.media.label(), top.confidence * 100)
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    guesses: List[EpisodeGuess] = []
    for key, v in votes.items():
        info = meta[key]
        guesses.append(EpisodeGuess(
            episode_id=episode_id_str(info.season, info.episode),
            title=info.title, season=info.season, episode=info.episode,
            votes=v, total_samples=samples_with_match,
            mean_confidence=conf_sum[key] / v, method="acoustic",
        ))
    # rank by (votes, mean confidence)
    guesses.sort(key=lambda g: (g.votes, g.mean_confidence), reverse=True)

    # candidate pool: every episode key seen in any sample's top-N, ranked by
    # its best acoustic confidence (used to scope the phonetic fallback).
    pool = sorted(best_conf.keys(), key=lambda k: best_conf[k], reverse=True)
    return duration, guesses, pool


# ---------------------------------------------------------------------------
# Stage 2: scoped phonetic fallback
# ---------------------------------------------------------------------------
def phonetic_fallback(path: str, db: FingerprintDB, fp_cfg: FingerprintConfig,
                      cfg: dict, candidate_keys: List[Tuple],
                      points: List[float], sample_len: float,
                      transcriber) -> Optional[EpisodeGuess]:
    """Transcribe the sampled windows and run phonetic matching scoped to the
    acoustic candidates. Returns an EpisodeGuess or None."""
    import stt_utils
    from identify_audio import run_fuzzy_stage

    duration = af._probe_duration(path)
    windows = sample_windows(duration, points, sample_len)

    audio_cfg = cfg.get("audio", {})
    sr = audio_cfg.get("sample_rate", 16000)
    try:
        full = stt_utils.load_audio_mono16k(path, sr)
    except Exception as exc:
        logging.debug("  phonetic: could not load audio: %s", exc)
        return None

    texts: List[str] = []
    for (start_s, length_s) in windows:
        sub = full[int(start_s * 1000):int((start_s + length_s) * 1000)]
        try:
            t = transcriber.transcribe_segment(sub)
        except Exception as exc:
            logging.debug("  phonetic: transcribe failed: %s", exc)
            t = ""
        if t:
            texts.append(t)
    text = " ".join(texts).strip()
    if not text:
        logging.info("  phonetic fallback: no speech recognised")
        return None
    logging.info("  phonetic fallback transcript (%d chars): \"%s%s\"",
                 len(text), text[:80], "..." if len(text) > 80 else "")

    # scope to the acoustic candidates (subtitle rows for those episodes)
    scope_ids = db.media_ids_for_episodes(candidate_keys) if candidate_keys else []

    query_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
    rows = db.lookup(query_hashes, media_ids=scope_ids or None)
    results = score_matches(query_hashes, rows, cfg.get("matching", {}))
    if results:
        m = results[0].media
        return EpisodeGuess(
            episode_id=episode_id_str(m.season, m.episode),
            title=m.title, season=m.season, episode=m.episode,
            votes=1, total_samples=1, mean_confidence=results[0].confidence,
            method="phonetic",
        )

    # exact phonetic found nothing -> order-preserving fuzzy fallback
    fuzzy_results, _fc = run_fuzzy_stage(text, db, fp_cfg, cfg, scope_ids)
    if fuzzy_results:
        m = fuzzy_results[0].media
        return EpisodeGuess(
            episode_id=episode_id_str(m.season, m.episode),
            title=m.title, season=m.season, episode=m.episode,
            votes=1, total_samples=1, mean_confidence=fuzzy_results[0].confidence,
            method="fuzzy",
        )
    return None


# ---------------------------------------------------------------------------
# Orchestration for one file
# ---------------------------------------------------------------------------
def identify_one(path: str, db: FingerprintDB, fp_cfg: FingerprintConfig,
                 ac_cfg: AcousticConfig, cfg: dict, args,
                 transcriber_box: dict,
                 runtimes: Optional[dict]) -> FileResult:
    fname = os.path.basename(path)
    t0 = time.time()
    print(f"\n>>> {fname}")

    duration, guesses, pool = acoustic_identify(
        path, db, ac_cfg, args.points, args.sample_len,
        top_n=cfg.get("matching", {}).get("top_n_results", 5))

    review_conf = args.review_confidence
    min_agree = args.min_agreement

    best = guesses[0] if guesses else None
    runner_up = guesses[1] if len(guesses) > 1 else None

    # Decide whether acoustic result is trustworthy.
    acoustic_ok = (
        best is not None
        and best.total_samples > 0
        and (best.votes / max(1, best.total_samples)) >= min_agree
        and best.mean_confidence >= review_conf
    )

    use_phonetic = (not args.no_phonetic) and (not acoustic_ok)
    if use_phonetic:
        top_k = cfg.get("hybrid", {}).get("top_candidates_count", 5)
        # Scope the phonetic search to the acoustic candidate POOL (union of
        # every sample's top-N), not just the single top-1 - otherwise one wrong
        # top-1 vote would force phonetic to confirm the wrong episode.
        candidate_keys = list(pool[:top_k])
        # If acoustic was very weak (below the reliable floor) its shortlist can
        # not be trusted at all, so widen the phonetic search to the whole DB.
        reliable_floor = cfg.get("hybrid", {}).get(
            "acoustic_shortlist_reliable", 0.15)
        if not best or best.mean_confidence < reliable_floor:
            logging.info("  acoustic unreliable (best %.0f%% < %.0f%%) -> "
                         "phonetic searches the FULL database",
                         (best.mean_confidence * 100) if best else 0,
                         reliable_floor * 100)
            candidate_keys = []
        else:
            logging.info("  acoustic weak/ambiguous -> phonetic fallback "
                         "(scoped to %d candidate episode(s))",
                         len(candidate_keys))
        # lazily construct the transcriber only when first needed
        if transcriber_box.get("t") is None and not transcriber_box.get("failed"):
            try:
                import stt_utils
                transcriber_box["t"] = stt_utils.get_transcriber(cfg)
            except Exception as exc:
                logging.warning("  could not init STT engine: %s", exc)
                transcriber_box["failed"] = True
        transcriber = transcriber_box.get("t")
        if transcriber is not None:
            ph = phonetic_fallback(path, db, fp_cfg, cfg, candidate_keys,
                                   args.points, args.sample_len, transcriber)
            if ph is not None:
                # prefer phonetic only if it's confident, else keep acoustic best
                if ph.mean_confidence >= cfg.get("hybrid", {}).get(
                        "phonetic_confirm_threshold", 0.15):
                    best = ph

    # Build result + review flag
    notes_parts: List[str] = []
    needs_review = False
    if best is None:
        needs_review = True
        notes_parts.append("no match")
    else:
        agree_ratio = best.votes / max(1, best.total_samples) \
            if best.method == "acoustic" else 1.0
        if best.mean_confidence < review_conf:
            needs_review = True
            notes_parts.append(f"low confidence {best.mean_confidence:.0%}")
        if best.method == "acoustic" and agree_ratio < min_agree:
            needs_review = True
            notes_parts.append(
                f"samples disagree ({best.votes}/{best.total_samples})")
        if runner_up and best.method == "acoustic" \
                and runner_up.votes == best.votes:
            needs_review = True
            notes_parts.append(f"tie with {runner_up.episode_id}")

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
        flag = "  ⚠ REVIEW" if needs_review else "  ✓"
        print(f"    => {best.episode_id}  {best.title}  "
              f"[{best.method}, conf {best.mean_confidence:.0%}, "
              f"{best.votes}/{best.total_samples} samples]{flag}")
        if result.notes:
            print(f"       note: {result.notes}")
    else:
        print("    => UNKNOWN  ⚠ REVIEW (no match)")
    return result


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
                    "acoustic sampling (with phonetic fallback).")
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
    ap.add_argument("--no-phonetic", action="store_true",
                    help="acoustic only; skip the phonetic fallback (fastest)")
    ap.add_argument("--review-confidence", type=float, default=0.35,
                    help="flag for manual review below this confidence "
                         "(default 0.35)")
    ap.add_argument("--min-agreement", type=float, default=0.5,
                    help="fraction of samples that must agree on the winner "
                         "before it is trusted (default 0.5)")
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

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.exists(db_path):
        print(f"ERROR: fingerprint DB not found: {db_path}", file=sys.stderr)
        print("Build one first from your subtitles, e.g.:\n"
              "  python create_fingerprint.py --dir /path/to/subs", file=sys.stderr)
        return 2
    db = FingerprintDB(db_path)
    fp_cfg = FingerprintConfig.from_config(cfg)
    ac_cfg = AcousticConfig.from_config(cfg)

    # verify fpcalc up front so batch runs fail fast with a clear message
    try:
        af.check_fpcalc(ac_cfg.fpcalc_path)
    except FpcalcNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

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

    print("=" * 70)
    print("  DVD EPISODE IDENTIFICATION")
    print("=" * 70)
    print(f"  reference DB : {db_path}")
    print(f"  files        : {len(media)}")
    print(f"  sample points: {', '.join(f'{p:.0%}' for p in args.points)} "
          f"({args.sample_len:.0f}s each)")
    print(f"  phonetic     : {'off' if args.no_phonetic else 'on (fallback)'}")

    transcriber_box: dict = {}
    results: List[FileResult] = []
    for path in media:
        if not os.path.exists(path):
            print(f"\n>>> {os.path.basename(path)}\n    (file not found, skipped)")
            continue
        try:
            results.append(identify_one(path, db, fp_cfg, ac_cfg, cfg, args,
                                        transcriber_box, runtimes))
        except FpcalcNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    # summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    ok = [r for r in results if not r.needs_review]
    review = [r for r in results if r.needs_review]
    print(f"  identified confidently : {len(ok)}/{len(results)}")
    for r in ok:
        g = r.to_row()
        print(f"    ✓ {g['filename']:<45.45}  -> {g['episode_id']}  "
              f"({g['method']}, {g['confidence']:.0%})")
    if review:
        print(f"\n  needs manual review    : {len(review)}")
        for r in review:
            g = r.to_row()
            print(f"    ⚠ {g['filename']:<45.45}  -> {g['episode_id']}  "
                  f"{('('+g['notes']+')') if g['notes'] else ''}")

    if args.csv:
        write_csv(results, args.csv)
        print(f"\n  CSV  written: {args.csv}")
    if args.json:
        write_json(results, args.json)
        print(f"  JSON written: {args.json}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
