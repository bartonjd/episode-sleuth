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
    phonetic_token_stream, score_fuzzy_matches, FuzzyConfig, MediaInfo,
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


def run_live(db, fp_cfg, transcriber, cfg, max_seconds=None, once=False,
             use_phonetic=True, use_acoustic=False, ac_cfg=None):
    audio_cfg = cfg.get("audio", {})
    match_cfg = cfg.get("matching", {})
    sr = audio_cfg.get("sample_rate", 16000)
    window_s = audio_cfg.get("chunk_seconds", 8)
    overlap_s = audio_cfg.get("overlap_seconds", 2)
    threshold = match_cfg.get("confidence_threshold", 0.15)
    min_matches = match_cfg.get("min_matches", 3)

    if use_acoustic:
        import acoustic_fingerprint as af
        ac_threshold = ac_cfg.confidence_threshold
        # Acoustic needs a longer window to produce enough frames.
        ac_window_bytes = ac_cfg.query_chunk_seconds * sr * 2
        ac_buf = bytearray()
        last_ac_eval = 0.0

    pa, stream, fpb = open_microphone(sr)
    methods = "+".join([m for m, on in (("phonetic", use_phonetic),
                                        ("acoustic", use_acoustic)) if on])
    logging.info("Listening on microphone (%d Hz) [%s]. Press Ctrl+C to stop.",
                 sr, methods)

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
            if use_acoustic:
                ac_buf.extend(data)
                if len(ac_buf) > ac_window_bytes:
                    del ac_buf[:len(ac_buf) - ac_window_bytes]

            if len(buf) >= window_bytes:
                # --- phonetic path ---
                if use_phonetic and transcriber is not None:
                    seg = pcm_to_segment(bytes(buf[-window_bytes:]), sr)
                    text = transcriber.transcribe_segment(seg)
                    if text:
                        logging.info("heard: %s", text[:70])
                        new_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
                        recent_hashes.extend(new_hashes)
                        query = list(recent_hashes)
                        rows = db.lookup(query)
                        results = score_matches(query, rows, match_cfg)
                        if results and results[0].confidence >= threshold \
                                and results[0].match_count >= min_matches:
                            print_live_result(results, threshold, method="phonetic")
                            if once:
                                break
                    elif once and not use_acoustic:
                        logging.info("(no speech detected in window)")
                # slide window forward
                del buf[:step_bytes]

            # --- acoustic path (evaluated on its own cadence) ---
            if use_acoustic and len(ac_buf) >= ac_window_bytes:
                now = time.time()
                if now - last_ac_eval >= max(1, window_s - overlap_s):
                    last_ac_eval = now
                    ac_results = af.match_acoustic_pcm(
                        bytes(ac_buf), sr, 1, db, ac_cfg)
                    if ac_results and ac_results[0].confidence >= ac_threshold:
                        print_live_result(ac_results, ac_threshold, method="acoustic")
                        if once:
                            break

            if max_seconds and (time.time() - start_time) > max_seconds:
                logging.info("Reached time limit (%ss).", max_seconds)
                break
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def print_live_result(results, threshold, method="phonetic"):
    best = results[0]
    print("\n" + "=" * 60)
    print(f">>> IDENTIFIED ({method}): {best.media.label()}")
    if method == "acoustic":
        print(f"    confidence : {best.confidence:.1%}  "
              f"({best.matched_frames}/{best.query_frames} frames)")
        if getattr(best, "ref_start_ms", None) is not None:
            print(f"    approx time: {best.ref_start_ms/1000:.0f}s into source")
    else:
        print(f"    confidence : {best.confidence:.1%}  ({best.match_count} hash hits)")
        if getattr(best, "window_start_ms", None) is not None:
            print(f"    approx time: {best.window_start_ms/1000:.0f}s into source")
    if len(results) > 1:
        runners = ", ".join(f"{r.media.label()} ({r.confidence:.0%})"
                            for r in results[1:3])
        print(f"    runners-up : {runners}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# File-based identification helpers
# ---------------------------------------------------------------------------
# Transcription lives here (identification of UNKNOWN audio) — never in the
# reference-building scripts, which use subtitles (phonetic) and Chromaprint
# (acoustic) directly.

def _chunk_segment(seg, chunk_seconds, overlap_seconds):
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
    chunks = list(_chunk_segment(seg, chunk_s, overlap_s))
    for i, (start_ms, end_ms, sub) in enumerate(chunks, 1):
        text = transcriber.transcribe_segment(sub)
        if text:
            results.append((start_ms, end_ms, text))
        logging.info("  transcribed chunk %d/%d (%.0fs) %s",
                     i, len(chunks), start_ms / 1000.0,
                     ("-> " + text[:50]) if text else "(silence)")
    return results


def identify_audio_file(path, db, fp_cfg, transcriber, cfg):
    """Phonetic identification of a recorded file: transcribe -> match."""
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


def _load_candidate_streams(db, media_ids, fp_cfg):
    """Load ``media_id -> (MediaInfo, ref_tokens, ref_starts)`` for the fuzzy
    matcher. Only media rows that actually have a stored token stream are
    returned (subtitle rows); acoustic-only media-file rows are skipped."""
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


def run_fuzzy_stage(query_text, db, fp_cfg, cfg, candidate_ids):
    """Order-preserving phonetic LCS matching as a fallback / confirmation when
    exact shingle-hash matching is weak (STT word errors).

    Searches the supplied ``candidate_ids`` first (the acoustic shortlist) and
    widens to every media with a token stream if those yield nothing. Returns a
    list of MatchResult (possibly empty) and the FuzzyConfig that was used.
    """
    fuzzy_cfg = FuzzyConfig.from_config(cfg)
    if not fuzzy_cfg.enabled or not query_text.strip():
        return [], fuzzy_cfg
    q_tokens = phonetic_token_stream(query_text, fp_cfg)
    if len(q_tokens) < fuzzy_cfg.min_query_tokens:
        logging.info("  fuzzy: query too short (%d < %d tokens), skipping",
                     len(q_tokens), fuzzy_cfg.min_query_tokens)
        return [], fuzzy_cfg

    top_k = cfg.get("hybrid", {}).get("top_candidates_count", 5)
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
    # runner-up; otherwise the match is ambiguous and we return nothing so the
    # caller falls back to the safer exact / acoustic verdict.
    if len(results) >= 2:
        margin = results[0].confidence - results[1].confidence
        if margin < fuzzy_cfg.min_margin:
            logging.info("  fuzzy: ambiguous (%.0f%% vs %.0f%%, margin %.0f%% < "
                         "%.0f%%) - rejecting", results[0].confidence * 100,
                         results[1].confidence * 100, margin * 100,
                         fuzzy_cfg.min_margin * 100)
            return [], fuzzy_cfg
    return results, fuzzy_cfg


def identify_hybrid_file(path, db, fp_cfg, ac_cfg, transcriber, cfg):
    """Two-stage hybrid identification (the recommended, Shazam-like mode).

    Stage 1 - ACOUSTIC SHORTLIST: fingerprint the audio and acoustically rank
              the reference media, keeping the top ``top_candidates_count``
              episodes as candidates. This is fast and noise-tolerant (uses the
              recall-focused acoustic config).
    Stage 2 - SCOPED PHONETIC: transcribe the audio and run phonetic matching
              *only against the shortlisted candidates*. Because the phonetic
              fingerprint table is large, scoping it to a handful of episodes is
              dramatically faster than a full-database search, while phonetic
              precision confirms the exact episode.

    If the acoustic stage finds no candidates at all, the phonetic stage falls
    back to a full-database search so identification still succeeds.
    """
    import acoustic_fingerprint as af

    hyb_cfg = cfg.get("hybrid", {})
    top_k = hyb_cfg.get("top_candidates_count", 5)
    ac_stage_thr = hyb_cfg.get("acoustic_shortlist_threshold", 0.0)
    phon_thr = hyb_cfg.get("phonetic_confirm_threshold",
                           cfg.get("matching", {}).get("confidence_threshold", 0.15))

    print("\n" + "=" * 60)
    print("HYBRID IDENTIFICATION (acoustic shortlist -> scoped phonetic)")
    print("=" * 60)

    # ---- Stage 1: acoustic shortlist ----------------------------------------
    t0 = time.time()
    logging.info("Stage 1/2: acoustic shortlist of %s ...", os.path.basename(path))
    ac_results = af.shortlist_candidates(path, db, ac_cfg, top_n=top_k)
    # apply optional shortlist threshold (keep at least the single best)
    if ac_stage_thr > 0 and ac_results:
        kept = [r for r in ac_results if r.confidence >= ac_stage_thr]
        ac_results = kept or ac_results[:1]
    t_ac = time.time() - t0

    # Resolve the acoustic candidates (which come from media-FILE rows) to ALL
    # media ids for the same episodes, including the SUBTITLE rows that actually
    # hold the phonetic fingerprints. Scoping by raw media_id alone would miss
    # them, because subtitles and media files are stored as separate media rows.
    episode_keys = [(r.media.title, r.media.season, r.media.episode)
                    for r in ac_results]
    candidate_ids = db.media_ids_for_episodes(episode_keys) if episode_keys else []
    print(f"\n[Stage 1] Acoustic shortlist  ({t_ac:.2f}s)  "
          f"-> {len(ac_results)} candidate episode(s) "
          f"({len(candidate_ids)} media row(s) to confirm):")
    if ac_results:
        for i, r in enumerate(ac_results, 1):
            print(f"    {i}. {r.media.label():38} "
                  f"acoustic={r.confidence:5.1%}")
    else:
        print("    (none - acoustic produced no candidates; "
              "phonetic will search the full database)")

    # ---- Stage 2: scoped phonetic -------------------------------------------
    t1 = time.time()
    logging.info("Stage 2/2: scoped phonetic confirmation ...")
    phon_results = []
    fuzzy_results = []
    text = ""
    if transcriber is not None:
        cues = transcribe_file(path, transcriber, cfg)
        text = " ".join(t for (_a, _b, t) in cues)
        if text.strip():
            query_hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
            # scope phonetic search to acoustic candidates (None => full DB)
            scope = candidate_ids if candidate_ids else None
            rows = db.lookup(query_hashes, media_ids=scope)
            phon_results = score_matches(query_hashes, rows, cfg.get("matching", {}))
            if not phon_results and scope is not None:
                # candidates didn't contain the dialogue; widen to full DB
                logging.info("  no phonetic hit within shortlist; "
                             "widening to full database")
                rows = db.lookup(query_hashes)
                phon_results = score_matches(query_hashes, rows,
                                             cfg.get("matching", {}))
        else:
            logging.info("  no speech recognised for phonetic confirmation")
    else:
        logging.warning("  STT unavailable; cannot run phonetic stage")
    t_phon = time.time() - t1

    print(f"\n[Stage 2] Scoped phonetic     ({t_phon:.2f}s)  "
          f"-> searched {len(candidate_ids) or 'all'} episode(s):")
    if phon_results:
        for i, r in enumerate(phon_results[:5], 1):
            flag = ">>" if (i == 1 and r.confidence >= phon_thr) else "  "
            print(f"  {flag} {i}. {r.media.label():38} "
                  f"phonetic={r.confidence:5.1%} ({r.match_count} hits)")
    else:
        print("    (no phonetic match)")

    # ---- Stage 2b: fuzzy phonetic fallback ----------------------------------
    # Exact shingle-hash matching can collapse on noisy STT output (one wrong
    # word breaks every shingle that overlaps it). When the exact result is
    # missing or below threshold, fall back to the order-preserving LCS matcher,
    # which tolerates dropped / mis-heard / inserted words.
    best_exact = phon_results[0] if phon_results else None
    need_fuzzy = (best_exact is None) or (best_exact.confidence < phon_thr)
    if need_fuzzy and text.strip():
        t2 = time.time()
        logging.info("Stage 2b/2: exact phonetic weak -> fuzzy LCS fallback ...")
        fuzzy_results, fuzzy_cfg = run_fuzzy_stage(
            text, db, fp_cfg, cfg, candidate_ids)
        t_fuzzy = time.time() - t2
        t_phon += t_fuzzy
        print(f"\n[Stage 2b] Fuzzy phonetic LCS ({t_fuzzy:.2f}s)  "
              f"-> order-preserving match (min ratio "
              f"{fuzzy_cfg.min_lcs_ratio:.0%}):")
        if fuzzy_results:
            for i, r in enumerate(fuzzy_results[:5], 1):
                flag = ">>" if i == 1 else "  "
                print(f"  {flag} {i}. {r.media.label():38} "
                      f"fuzzy={r.confidence:5.1%} "
                      f"(LCS {r.match_count}/{r.query_count})")
        else:
            print("    (no fuzzy match above ratio)")

    # ---- Final verdict -------------------------------------------------------
    print("\n" + "-" * 60)
    best = phon_results[0] if phon_results else None
    method = "hybrid (acoustic shortlist + phonetic confirm)"
    # Prefer exact phonetic if it clears the threshold; otherwise consider fuzzy.
    if not (best and best.confidence >= phon_thr) and fuzzy_results:
        fbest = fuzzy_results[0]
        if best is None or fbest.confidence > best.confidence:
            best = fbest
            method = "hybrid (acoustic shortlist + fuzzy phonetic LCS)"
    if best and best.confidence >= phon_thr:
        print(f">>> IDENTIFIED: {best.media.label()}  ({best.confidence:.1%})")
        print(f"    method: {method}")
    elif best:
        print(f">>> Best guess: {best.media.label()}  "
              f"({best.confidence:.1%}) - below threshold {phon_thr:.0%}")
    elif ac_results:
        b = ac_results[0]
        print(f">>> Best guess (acoustic only): {b.media.label()}  "
              f"({b.confidence:.1%})")
    else:
        print(">>> No match found by either stage.")
    print(f"    total time: {t_ac + t_phon:.2f}s "
          f"(acoustic {t_ac:.2f}s + phonetic {t_phon:.2f}s)")
    print("=" * 60)
    return best, ac_results


def run_hybrid_live(db, fp_cfg, ac_cfg, transcriber, cfg,
                    max_seconds=None, once=False):
    """Live two-stage hybrid identification from the microphone.

    Acoustic shortlisting runs on a rolling audio window to keep a current set
    of candidate episodes; phonetic matching of the transcribed window is then
    scoped to those candidates for a fast, precise confirmation.
    """
    import acoustic_fingerprint as af

    audio_cfg = cfg.get("audio", {})
    match_cfg = cfg.get("matching", {})
    hyb_cfg = cfg.get("hybrid", {})
    sr = audio_cfg.get("sample_rate", 16000)
    window_s = audio_cfg.get("chunk_seconds", 8)
    overlap_s = audio_cfg.get("overlap_seconds", 2)
    top_k = hyb_cfg.get("top_candidates_count", 5)
    phon_thr = hyb_cfg.get("phonetic_confirm_threshold",
                           match_cfg.get("confidence_threshold", 0.15))
    min_matches = match_cfg.get("min_matches", 3)

    ac_window_bytes = ac_cfg.query_chunk_seconds * sr * 2
    ac_buf = bytearray()
    candidate_ids = []

    pa, stream, fpb = open_microphone(sr)
    logging.info("Listening on microphone (%d Hz) [hybrid]. Press Ctrl+C to stop.", sr)

    window_bytes = window_s * sr * 2
    step_bytes = max(1, (window_s - overlap_s)) * sr * 2
    buf = bytearray()
    recent_hashes = collections.deque(maxlen=2000)
    recent_text = collections.deque(maxlen=40)   # for fuzzy LCS fallback
    start_time = time.time()
    last_ac_eval = 0.0
    try:
        while True:
            data = stream.read(fpb, exception_on_overflow=False)
            buf.extend(data)
            ac_buf.extend(data)
            if len(ac_buf) > ac_window_bytes:
                del ac_buf[:len(ac_buf) - ac_window_bytes]

            # Stage 1: refresh acoustic shortlist on its own cadence
            now = time.time()
            if len(ac_buf) >= ac_window_bytes and \
                    now - last_ac_eval >= max(1, window_s - overlap_s):
                last_ac_eval = now
                ac_results = af.shortlist_candidates_pcm(
                    bytes(ac_buf), sr, 1, db, ac_cfg, top_n=top_k)
                # resolve to all media rows (incl. subtitle rows) for the same
                # episodes so the phonetic stage can actually find dialogue
                episode_keys = [(r.media.title, r.media.season, r.media.episode)
                                for r in ac_results]
                candidate_ids = (db.media_ids_for_episodes(episode_keys)
                                 if episode_keys else [])
                if candidate_ids:
                    logging.info("shortlist: %s",
                                 ", ".join(f"{r.media.label()} ({r.confidence:.0%})"
                                           for r in ac_results[:3]))

            # Stage 2: scoped phonetic on the transcribed window
            if len(buf) >= window_bytes and transcriber is not None:
                seg = pcm_to_segment(bytes(buf[-window_bytes:]), sr)
                text = transcriber.transcribe_segment(seg)
                if text:
                    logging.info("heard: %s", text[:70])
                    recent_hashes.extend(h for (h, _s) in fingerprint_text(text, fp_cfg))
                    recent_text.append(text)
                    query = list(recent_hashes)
                    scope = candidate_ids if candidate_ids else None
                    rows = db.lookup(query, media_ids=scope)
                    results = score_matches(query, rows, match_cfg)
                    if not results and scope is not None:
                        rows = db.lookup(query)
                        results = score_matches(query, rows, match_cfg)
                    if results and results[0].confidence >= phon_thr \
                            and results[0].match_count >= min_matches:
                        print_live_result(results, phon_thr, method="hybrid")
                        if once:
                            break
                    else:
                        # exact matching weak -> fuzzy order-preserving LCS
                        fuzzy_results, _fz = run_fuzzy_stage(
                            " ".join(recent_text), db, fp_cfg, cfg, candidate_ids)
                        if fuzzy_results and fuzzy_results[0].confidence >= phon_thr:
                            print_live_result(fuzzy_results, phon_thr,
                                              method="hybrid-fuzzy")
                            if once:
                                break
                del buf[:step_bytes]

            if max_seconds and (time.time() - start_time) > max_seconds:
                logging.info("Reached time limit (%ss).", max_seconds)
                break
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def run_file_fallback(path, db, fp_cfg, transcriber, cfg,
                      use_phonetic=True, use_acoustic=False, ac_cfg=None,
                      use_hybrid=False):
    """Identify from a recorded file (useful when no mic is available)."""
    if use_hybrid:
        identify_hybrid_file(path, db, fp_cfg, ac_cfg, transcriber, cfg)
    elif use_acoustic:
        identify_combined(
            path, db, fp_cfg, ac_cfg, transcriber, cfg,
            use_phonetic, use_acoustic)
    else:
        identify_audio_file(path, db, fp_cfg, transcriber, cfg)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Identify live audio against the fingerprint DB.")
    parser.add_argument("--seconds", type=int, help="Stop after N seconds")
    parser.add_argument("--once", action="store_true",
                        help="Stop after the first confident match")
    parser.add_argument("--acoustic", action="store_true",
                        help="Use ACOUSTIC (Chromaprint) matching only "
                             "(identify by sound, no transcription)")
    parser.add_argument("--both", action="store_true",
                        help="Use BOTH phonetic (dialogue) and acoustic (sound) "
                             "matching independently and report whichever is "
                             "more confident")
    parser.add_argument("--hybrid", action="store_true",
                        help="RECOMMENDED two-stage mode: acoustic shortlist "
                             "first, then precise phonetic confirmation scoped "
                             "to those candidates (fast + robust, Shazam-like)")
    parser.add_argument("--from-file",
                        help="Identify from a recorded audio file instead of the mic")
    parser.add_argument("--config")
    parser.add_argument("--db")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)

    # Decide which method(s) to use. Default is phonetic only.
    use_hybrid = args.hybrid
    # Hybrid needs both engines (acoustic shortlist + phonetic confirm).
    use_acoustic = args.acoustic or args.both or use_hybrid
    use_phonetic = args.both or use_hybrid or not args.acoustic

    import acoustic_fingerprint as af
    ac_cfg = af.AcousticConfig.from_config(cfg)

    db_path = args.db or cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    db = FingerprintDB(db_path)

    stats = db.stats()
    if use_phonetic and stats["fingerprints"] == 0 and not use_acoustic:
        logging.error("Phonetic fingerprint database is empty. "
                      "Run create_fingerprint.py first.")
        db.close()
        return 1
    if use_acoustic and stats.get("acoustic_segments", 0) == 0 and not use_phonetic:
        logging.error("Acoustic fingerprint database is empty. "
                      "Run create_acoustic_fingerprint.py first.")
        db.close()
        return 1

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
        if args.from_file:
            run_file_fallback(args.from_file, db, fp_cfg, transcriber, cfg,
                              use_phonetic=use_phonetic, use_acoustic=use_acoustic,
                              ac_cfg=ac_cfg, use_hybrid=use_hybrid)
        elif use_hybrid:
            run_hybrid_live(db, fp_cfg, ac_cfg, transcriber, cfg,
                            max_seconds=args.seconds, once=args.once)
        else:
            run_live(db, fp_cfg, transcriber, cfg,
                     max_seconds=args.seconds, once=args.once,
                     use_phonetic=use_phonetic, use_acoustic=use_acoustic,
                     ac_cfg=ac_cfg)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
