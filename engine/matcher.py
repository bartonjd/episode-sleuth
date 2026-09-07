#!/usr/bin/env python3
"""Core single-file identification.

``identify_one`` is the heart of the engine: given one media file it probes the
duration, extracts a handful of short audio windows, transcribes them, matches
the dialogue against the reference DB (exact phonetic first, fuzzy fallback
second), applies the metadata boosts and returns a fully-populated
``FileResult`` including the Plex rename suggestion.

Everything in this module is thread-safe: ``identify_one`` opens its own DB
connection and only the shared ``transcriber`` is reused across threads (each
transcription builds its own recogniser internally).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import List, Optional, Tuple

from fingerprint_core import FingerprintDB, FingerprintConfig, score_matches

from .types import EpisodeGuess, FileResult
from .discovery import episode_id_str, build_suggested_filename
from .scoring import (
    _build_weighted_query, apply_metadata_boosts,
    _adaptive_review_threshold, run_fuzzy_stage,
)

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
# Transcription of the sampled windows
# ---------------------------------------------------------------------------
def transcribe_samples(path: str, windows: List[Tuple[float, float]],
                       transcriber, sample_rate: int,
                       cancel_check=None
                       ) -> Tuple[List[Tuple[float, str]], int]:
    """Extract each sample window with ffmpeg and transcribe it.

    Returns ``(per_window, got)`` where ``per_window`` is a list of
    ``(start_s, text)`` for every window that produced speech (start time kept so
    the caller can time-weight each sample), and ``got`` is that count.

    ``cancel_check`` is an optional zero-arg callable; when it returns True the
    loop stops early (between windows) so a user Cancel takes effect within one
    sample instead of running the whole file to completion.
    """
    import stt_utils

    per_window: List[Tuple[float, str]] = []
    got = 0
    tmpdir = tempfile.mkdtemp(prefix="dvdid_")
    try:
        for i, (start_s, length_s) in enumerate(windows):
            if cancel_check is not None and cancel_check():
                break
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
                per_window.append((start_s, t))
                got += 1
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return per_window, got


def _duration_tie_break(results, db, file_duration_s: float) -> None:
    """Reorder a near-tied ``results`` list to prefer the closer-duration match.

    When the top two candidates score almost identically, the one whose stored
    expected runtime is closest to the file's actual duration is the better bet.
    Operates in place on the first two elements only (the near-tie), and does
    nothing if neither candidate has a stored duration. Never raises.
    """
    if len(results) < 2 or file_duration_s <= 0:
        return
    try:
        pair = results[:2]
        best_i = None
        best_diff = None
        for i, r in enumerate(pair):
            minfo = db.media_info(r.media_id)
            exp = minfo.duration_seconds if minfo is not None else None
            if not exp or exp <= 0:
                continue
            diff = abs(file_duration_s - float(exp))
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_i = i
        # If the second candidate is a clearly closer duration match, promote it.
        if best_i == 1:
            results[0], results[1] = results[1], results[0]
    except Exception:  # a tie-break must never break identification
        return


# ---------------------------------------------------------------------------
# Orchestration for one file
# ---------------------------------------------------------------------------
def identify_one(path: str, db_path: str, fp_cfg: FingerprintConfig,
                 cfg: dict, args, transcriber,
                 runtimes: Optional[dict], cancel_check=None) -> FileResult:
    """Identify a single file by dialogue. Opens its own DB connection so it is
    safe to run in a worker thread; the ``transcriber`` is shared (each
    transcription builds its own recogniser internally).

    ``cancel_check`` is an optional zero-arg callable; when it returns True the
    per-file work bails out early (checked between transcription windows) so a
    user Cancel is honoured promptly instead of running the file to completion.
    """
    fname = os.path.basename(path)
    t0 = time.time()
    _log(f"\n>>> {fname}")

    db = FingerprintDB(db_path)
    try:
        duration = _probe_duration(path)
        windows = sample_windows(duration, args.points, args.sample_len)

        sr = cfg.get("audio", {}).get("sample_rate", 16000)
        best: Optional[EpisodeGuess] = None
        best_match_count = 0
        best_media_id: Optional[int] = None  # DB id of the winning candidate
        runner_up_margin: Optional[float] = None  # gap to the 2nd-best candidate
        notes_parts: List[str] = []
        boosted = False

        # Metadata context for the confidence boosts: the show the user told us
        # this batch belongs to (exact-match boost) and the episode title parsed
        # from THIS file's own name, if any (fuzzy episode-title boost).
        expected_show = getattr(args, "show_title", None)
        file_season = file_episode = None
        try:
            import subtitle_utils as _su
            file_season, file_episode, query_episode_title = \
                _su.parse_episode_info(fname)
        except Exception:
            query_episode_title = None

        if transcriber is None:
            notes_parts.append("STT engine unavailable")
            per_window, got = [], 0
        else:
            per_window, got = transcribe_samples(
                path, windows, transcriber, sr, cancel_check=cancel_check)

        # If the user cancelled mid-file, return a lightweight placeholder rather
        # than a misleading "no match" verdict; the caller discards it.
        if cancel_check is not None and cancel_check():
            return FileResult(
                filename=fname, path=path, duration_s=duration, guess=None,
                needs_review=True, notes="cancelled",
                elapsed_s=time.time() - t0,
                name_status="unknown", suggested_filename="")

        text = " ".join(t for _s, t in per_window).strip()
        if text:
            logging.info("  transcript (%d chars, %d/%d samples): \"%s%s\"",
                         len(text), got, len(windows), text[:80],
                         "..." if len(text) > 80 else "")
            # Stage 1: exact phonetic shingle match, time-weighted per sample.
            query_hashes, query_weights = _build_weighted_query(
                per_window, duration, fp_cfg)
            rows = db.lookup(query_hashes)
            results = score_matches(query_hashes, rows, cfg.get("matching", {}),
                                    query_weights=query_weights)
            if results:
                notes = apply_metadata_boosts(
                    results, expected_show, query_episode_title)
                boosted = bool(notes)
                # Duration tie-break: when the top two candidates are within a
                # hair of each other, prefer the one whose stored expected
                # runtime is closest to this file's actual duration. This nudges
                # near-ties toward the episode that also matches on length,
                # instead of leaving the order to scoring noise alone.
                min_margin = cfg.get("matching", {}).get("min_exact_margin", 0.06)
                if (duration > 0 and len(results) >= 2
                        and (results[0].confidence - results[1].confidence)
                        < min_margin):
                    _duration_tie_break(results, db, duration)
                m = results[0].media
                if notes:
                    notes_parts.append(", ".join(notes))
                best = EpisodeGuess(
                    episode_id=episode_id_str(m.season, m.episode),
                    title=(getattr(m, "show_title", None) or m.title),
                    season=m.season, episode=m.episode,
                    votes=got, total_samples=len(windows),
                    mean_confidence=results[0].confidence, method="phonetic",
                    episode_title=(getattr(m, "episode_title", None) or ""),
                )
                best_match_count = results[0].match_count
                best_media_id = results[0].media_id
                # Ambiguity signal: how far ahead is the winner of the runner-up?
                # (computed on the same, post-boost, sorted list)
                if len(results) >= 2:
                    runner_up_margin = results[0].confidence - results[1].confidence
            else:
                # Stage 2: order-preserving fuzzy fallback (tolerates STT errors)
                fuzzy_results, _fc = run_fuzzy_stage(text, db, fp_cfg, cfg, [])
                if fuzzy_results:
                    notes = apply_metadata_boosts(
                        fuzzy_results, expected_show, query_episode_title)
                    boosted = bool(notes)
                    m = fuzzy_results[0].media
                    if notes:
                        notes_parts.append(", ".join(notes))
                    best = EpisodeGuess(
                        episode_id=episode_id_str(m.season, m.episode),
                        title=(getattr(m, "show_title", None) or m.title),
                        season=m.season, episode=m.episode,
                        votes=got, total_samples=len(windows),
                        mean_confidence=fuzzy_results[0].confidence,
                        method="fuzzy",
                        episode_title=(getattr(m, "episode_title", None) or ""),
                    )
                    best_match_count = fuzzy_results[0].match_count
                    best_media_id = fuzzy_results[0].media_id
        elif transcriber is not None:
            notes_parts.append("no speech recognised")

        # Build review flag (with an adaptive, evidence-aware threshold)
        review_conf = args.review_confidence
        needs_review = False
        if best is None:
            needs_review = True
            if not notes_parts:
                notes_parts.append("no match")
        else:
            eff_review = _adaptive_review_threshold(
                review_conf, best_match_count, boosted)
            if best.mean_confidence < eff_review:
                needs_review = True
                notes_parts.append(f"low confidence {best.mean_confidence:.0%}")

            # Ambiguity gate: if a second episode scores almost as high, the
            # verdict is a near-tie and not trustworthy - flag for review rather
            # than silently asserting the winner. Only trips when the winner is
            # not already overwhelmingly strong.
            min_margin = cfg.get("matching", {}).get("min_exact_margin", 0.06)
            if (runner_up_margin is not None and runner_up_margin < min_margin
                    and best.mean_confidence < 0.90):
                needs_review = True
                notes_parts.append(
                    f"ambiguous: runner-up within {runner_up_margin:.0%}")

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

        # Episode-duration validation against the reference library. Each
        # reference episode may carry an expected ``duration_seconds`` (from the
        # TVMaze lookup or the subtitle heuristic at build time). If this file's
        # actual duration differs from the matched episode's expected duration by
        # more than the allowed fraction, flag it: a large length mismatch is a
        # strong sign the audio was matched to the wrong episode.
        if best is not None and best_media_id is not None and duration > 0:
            tol = float(cfg.get("matching", {}).get(
                "duration_tolerance_fraction", 0.15))
            expected_sec = None
            dur_source = None
            try:
                minfo = db.media_info(best_media_id)
                if minfo is not None:
                    expected_sec = minfo.duration_seconds
                    dur_source = minfo.duration_source
            except Exception:  # never let the check break identification
                expected_sec = None
            if expected_sec and expected_sec > 0:
                rel_diff = abs(duration - expected_sec) / float(expected_sec)
                if rel_diff > tol:
                    if dur_source in ("tvmaze", "manual"):
                        # Authoritative runtime (TVMaze lookup or a manual edit):
                        # a large mismatch is
                        # a strong sign of a wrong-episode match -> flag review.
                        needs_review = True
                        notes_parts.append(
                            f"duration mismatch: file is {duration/60:.0f}m, "
                            f"episode should be ~{expected_sec/60:.0f}m "
                            f"({rel_diff*100:.0f}% off)")
                    else:
                        # Subtitle-derived runtime is only a rough proxy (a
                        # truncated subtitle underestimates length), so note it
                        # for the reviewer's eyes but do NOT force a review flag.
                        notes_parts.append(
                            f"approx duration off ~{rel_diff*100:.0f}% "
                            f"(subtitle estimate ~{expected_sec/60:.0f}m)")

        # Naming verification: compare the current filename against what the
        # reference library says this episode should be called. This is the
        # core purpose of the tool (spot mislabelled rips, title new ones).
        name_status = "unknown"
        suggested_filename = ""
        if best is not None:
            ext = os.path.splitext(fname)[1]
            suggested_filename = build_suggested_filename(
                best.title, best.season, best.episode, best.episode_title, ext)
            if needs_review:
                # Not confident enough to assert the correct name.
                name_status = "unknown"
            elif (file_season is not None and file_episode is not None
                  and file_season == best.season
                  and file_episode == best.episode):
                # Same S/E number => correctly placed. Note: a differing
                # multi-part title format ("Part 1" vs "(1)") never triggers a
                # rename here because the verdict is number-based, honouring
                # identify.ignore_part_format_differences by construction.
                name_status = "correct"
            else:
                name_status = "rename"
                if file_season is not None and file_episode is not None:
                    notes_parts.append(
                        f"named {episode_id_str(file_season, file_episode)} "
                        f"but matches {best.episode_id}")
                else:
                    notes_parts.append("no S/E in filename")

        result = FileResult(
            filename=fname, path=path, duration_s=duration, guess=best,
            needs_review=needs_review, notes="; ".join(notes_parts),
            elapsed_s=time.time() - t0,
            name_status=name_status, suggested_filename=suggested_filename,
        )

        if best is not None:
            flag = "  ! REVIEW" if needs_review else "  OK"
            ep_disp = f" - {best.episode_title}" if best.episode_title else ""
            _log(f"    => {best.episode_id}{ep_disp}  ({best.title})  "
                 f"[{best.method}, conf {best.mean_confidence:.0%}]{flag}")
            if name_status == "correct":
                _log("       name: correct")
            elif name_status == "rename":
                _log(f"       name: should be \"{suggested_filename}\"")
            if result.notes:
                _log(f"       note: {result.notes}")
        else:
            _log(f"    => UNKNOWN  ! REVIEW ({result.notes or 'no match'})")
        return result
    finally:
        db.close()
