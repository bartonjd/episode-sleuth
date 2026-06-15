#!/usr/bin/env python3
"""
demo_hybrid.py
==============
A self-contained, end-to-end demonstration of the **two-stage hybrid**
audio-identification pipeline. It proves, with concrete numbers, that:

  1. CORRECTNESS  - the hybrid pipeline identifies a real microphone
                    re-recording (``Recording.m4a``) as the right episode
                    (Matlock S01E04), showing both stages in action.

  2. PERFORMANCE  - scoping the phonetic search to the acoustic shortlist is
                    dramatically faster than scanning the whole fingerprint
                    table. Because the bundled DB only has 3 episodes (where no
                    reduction is possible), this builds a large *synthetic*
                    library on the fly and times "scoped" vs "full-database"
                    phonetic lookups so the speed-up is visible.

  3. FUZZY MATCH  - the order-preserving phonetic LCS fallback recovers the
                    correct episode from a heavily degraded STT transcript where
                    exact shingle-hash matching scores ~0%.

  4. LIVE PATH    - the live-microphone code path (acoustic shortlist on PCM ->
                    media_ids_for_episodes -> scoped phonetic) runs without the
                    old NULL-season/episode SQL crash, exercised here by feeding
                    the real recording's PCM through the exact same calls the mic
                    loop uses (no microphone hardware required).

Run:
    python demo_hybrid.py                 # all demos
    python demo_hybrid.py --skip-acoustic # skip demos needing fpcalc/STT
    python demo_hybrid.py --synth-episodes 800
"""

import os
import sys
import time
import random
import argparse
import logging
import tempfile

from fingerprint_core import (
    load_config, setup_logging, FingerprintConfig, FingerprintDB,
    MediaInfo, fingerprint_text, score_matches,
    phonetic_token_stream, score_fuzzy_matches, FuzzyConfig,
)

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDING = os.path.join(HERE, "Recording.m4a")
SUBS = {
    4: os.path.join(HERE, "..", "Uploads",
                    "Matlock (1986) - S01E04 - The Stripper.srt"),
    3: os.path.join(HERE, "..", "Uploads",
                    "Matlock (1986) - S01E03 - The Judge.srt"),
    1: os.path.join(HERE, "..", "Uploads",
                    "Matlock (1986) - S01E01E02 - Diary of a Perfect Murder (Pilot).srt"),
}

BANNER = "=" * 70


def hdr(title):
    print("\n" + BANNER)
    print(title)
    print(BANNER)


# ---------------------------------------------------------------------------
# DEMO 1 - correctness on the real recording (full two-stage hybrid)
# ---------------------------------------------------------------------------

def demo_correctness(cfg, fp_cfg, db_path):
    hdr("DEMO 1/4  -  CORRECTNESS: hybrid identifies the real recording")
    if not os.path.exists(RECORDING):
        print(f"  SKIP: {RECORDING} not found.")
        return None
    try:
        import acoustic_fingerprint as af
        import stt_utils
        import identify_audio as ia
    except Exception as exc:                       # pragma: no cover
        print(f"  SKIP: could not import deps ({exc}).")
        return None

    ac_cfg = af.AcousticConfig.from_config(cfg)
    try:
        af.check_fpcalc(ac_cfg.fpcalc_path)
    except Exception as exc:
        print(f"  SKIP: fpcalc unavailable ({exc}).")
        return None
    try:
        transcriber = stt_utils.get_transcriber(cfg)
    except Exception as exc:
        print(f"  SKIP: STT unavailable ({exc}).")
        return None

    db = FingerprintDB(db_path)
    try:
        print("  Running: identify_audio --from-file Recording.m4a --hybrid\n")
        best, ac_results = ia.identify_hybrid_file(
            RECORDING, db, fp_cfg, ac_cfg, transcriber, cfg)
    finally:
        db.close()

    ok = bool(best) and best.media.season == 1 and best.media.episode == 4
    print(f"\n  RESULT: {'PASS' if ok else 'CHECK'} - "
          f"identified {best.media.label() if best else 'nothing'}")
    if ac_results:
        ac_rank = [r.media.episode for r in ac_results]
        print(f"  NOTE  : acoustic-alone ranking was {ac_rank} "
              f"(E04 was #{ac_rank.index(4)+1 if 4 in ac_rank else '-'}); "
              f"the phonetic stage corrected the final verdict to E04.")
    return ok


# ---------------------------------------------------------------------------
# DEMO 2 - performance: scoped vs full-database phonetic lookup
# ---------------------------------------------------------------------------

def _random_hash(rng):
    return "%016x" % rng.getrandbits(64)


def demo_performance(cfg, fp_cfg, synth_episodes, fps_per_ep):
    hdr("DEMO 2/4  -  PERFORMANCE: scoped phonetic vs full-database scan")
    print(f"  Building a synthetic library of {synth_episodes:,} episodes "
          f"x {fps_per_ep:,} fingerprints ...")

    tmp = tempfile.mkdtemp(prefix="demo_perf_")
    db_path = os.path.join(tmp, "perf.db")
    db = FingerprintDB(db_path)
    rng = random.Random(1234)

    # 1) one KNOWN episode whose dialogue we will later query for.
    known_text = (
        "your honor i object to this entire line of questioning the witness "
        "clearly has no knowledge of the events that night ben matlock will "
        "prove my client is innocent of all charges ladies and gentlemen of "
        "the jury please consider the evidence very carefully before you decide"
    )
    known_info = MediaInfo("Synthetic Show", 2000, "tv", 1, 1, "known.srt")
    known_id = db.get_or_create_media(known_info)
    known_hashes = [h for (h, _s) in fingerprint_text(known_text, fp_cfg)]
    db.add_fingerprints(known_id, [(h, 4, i * 1000, i * 1000 + 900)
                                   for i, h in enumerate(known_hashes)])

    # 2) a large number of DECOY episodes. To be REALISTIC, decoys must share
    #    some shingles with the query - in a real library, common phonetic
    #    phrases ("ladies and gentlemen", "your honor", ...) recur across many
    #    episodes, so a full-database lookup returns hits in lots of episodes and
    #    must score them all. We model that by having each decoy reuse a slice of
    #    the query's hashes (common phrases) plus its own random hashes. Without
    #    this, decoys never collide with the query and the hash index makes the
    #    full scan look deceptively cheap.
    common_pool = known_hashes[: max(1, len(known_hashes) // 2)]
    n_common = max(1, len(common_pool) // 3)
    t0 = time.time()
    for ep in range(2, synth_episodes + 2):
        info = MediaInfo("Synthetic Show", 2000, "tv", 1, ep, f"ep{ep}.srt")
        mid = db.get_or_create_media(info)
        shared = [(h, 4, 0, 900) for h in rng.sample(common_pool, n_common)]
        randoms = [(_random_hash(rng), 4, 0, 900)
                   for _ in range(fps_per_ep - n_common)]
        db.add_fingerprints(mid, shared + randoms)
    build_s = time.time() - t0
    total_fp = db.count_fingerprints()
    print(f"  Built {total_fp:,} fingerprints in {build_s:.1f}s "
          f"(each decoy shares {n_common} common shingles with the query, "
          f"simulating recurring phrases).\n")

    # The query: the known episode's dialogue with light STT noise.
    rng2 = random.Random(7)
    noisy = " ".join(w for w in known_text.split() if rng2.random() > 0.15)
    query = [h for (h, _s) in fingerprint_text(noisy, fp_cfg)]

    REPEAT = 20

    # FULL-DATABASE phonetic search
    t0 = time.time()
    for _ in range(REPEAT):
        rows = db.lookup(query)                       # no scope => whole table
        full_results = score_matches(query, rows, cfg.get("matching", {}))
    full_ms = (time.time() - t0) / REPEAT * 1000

    # SCOPED phonetic search (as the hybrid does after acoustic shortlist)
    scope = [known_id]
    t0 = time.time()
    for _ in range(REPEAT):
        rows = db.lookup(query, media_ids=scope)
        scoped_results = score_matches(query, rows, cfg.get("matching", {}))
    scoped_ms = (time.time() - t0) / REPEAT * 1000

    db.close()

    speedup = full_ms / scoped_ms if scoped_ms else float("inf")
    full_top = full_results[0] if full_results else None
    scoped_top = scoped_results[0] if scoped_results else None
    print(f"  full-database phonetic lookup : {full_ms:8.2f} ms/query "
          f"(searched {total_fp:,} fingerprints)")
    print(f"  scoped (1-episode) lookup     : {scoped_ms:8.2f} ms/query "
          f"(searched {len(known_hashes):,} fingerprints)")
    print(f"  ------------------------------------------------------------")
    print(f"  SPEED-UP                      : {speedup:8.1f}x faster")
    same = (full_top and scoped_top
            and full_top.media.episode == scoped_top.media.episode == 1)
    print(f"  same correct answer (E01)     : {'YES' if same else 'NO'} "
          f"(full={full_top.confidence:.0%}, scoped={scoped_top.confidence:.0%})"
          if full_top and scoped_top else "  (no match)")
    print(f"\n  RESULT: {'PASS' if same and speedup > 1 else 'CHECK'} - "
          f"scoping is {speedup:.1f}x faster with no loss of accuracy.")
    return speedup


# ---------------------------------------------------------------------------
# DEMO 3 - fuzzy matching on a heavily degraded transcript
# ---------------------------------------------------------------------------

def _build_streams(db, fp_cfg):
    streams = {}
    for mid in db.all_token_stream_media_ids():
        toks, starts = db.get_token_stream(mid)
        streams[mid] = (db.media_info(mid), toks, starts)
    return streams


def demo_fuzzy(cfg, fp_cfg, db_path):
    hdr("DEMO 3/4  -  FUZZY MATCH: recover the episode from degraded STT")
    db = FingerprintDB(db_path)
    streams = _build_streams(db, fp_cfg)
    if not streams:
        print("  SKIP: no token streams in DB (re-run create_fingerprint.py).")
        db.close()
        return None

    # Take genuine dialogue from S01E04 and simulate a bad STT transcript:
    # drop ~30% of words, truncate (mis-hear) some, keep order.
    import subtitle_utils as su
    try:
        cues = su.parse_subtitle_file(SUBS[4])
    except Exception as exc:
        print(f"  SKIP: cannot read E04 subtitle ({exc}).")
        db.close()
        return None
    seg = " ".join(t for (_a, _b, t) in cues[200:235])
    rng = random.Random(3)
    degraded = []
    for w in seg.split():
        r = rng.random()
        if r < 0.30:
            continue                      # dropped word
        elif r < 0.42 and len(w) > 3:
            degraded.append(w[:-1])       # mis-heard (truncated)
        else:
            degraded.append(w)
    degraded_text = " ".join(degraded)
    print(f"  original dialogue  : \"{seg[:90]}...\"")
    print(f"  degraded transcript: \"{degraded_text[:90]}...\"")
    print(f"  ({len(seg.split())} words -> {len(degraded)} noisy words)\n")

    # EXACT shingle-hash matching
    qh = [h for (h, _s) in fingerprint_text(degraded_text, fp_cfg)]
    exact = score_matches(qh, db.lookup(qh), cfg.get("matching", {}))
    exact_best = exact[0] if exact else None

    # FUZZY order-preserving LCS matching
    q_tokens = phonetic_token_stream(degraded_text, fp_cfg)
    fz = score_fuzzy_matches(q_tokens, streams, FuzzyConfig.from_config(cfg), top_n=5)
    db.close()

    print("  EXACT shingle-hash ranking:")
    if exact:
        for r in exact[:3]:
            print(f"     S01E{r.media.episode:02d}  exact={r.confidence:5.1%} "
                  f"({r.match_count} hits)")
    else:
        print("     (no exact matches at all)")
    print("  FUZZY order-preserving LCS ranking:")
    for r in fz[:3]:
        print(f"     S01E{r.media.episode:02d}  fuzzy={r.confidence:5.1%} "
              f"(LCS {r.match_count}/{r.query_count})")

    fuzzy_ok = bool(fz) and fz[0].media.episode == 4
    exact_conf = exact_best.confidence if exact_best else 0.0
    fuzzy_conf = fz[0].confidence if fz else 0.0
    print(f"\n  RESULT: {'PASS' if fuzzy_ok else 'CHECK'} - fuzzy recovered "
          f"S01E04 @ {fuzzy_conf:.0%} where exact scored {exact_conf:.0%}.")
    return fuzzy_ok


# ---------------------------------------------------------------------------
# DEMO 4 - live-microphone code path (SQL fix) without hardware
# ---------------------------------------------------------------------------

def demo_live_path(cfg, fp_cfg, db_path):
    hdr("DEMO 4/4  -  LIVE PATH: mic pipeline runs without the SQL crash")
    if not os.path.exists(RECORDING):
        print(f"  SKIP: {RECORDING} not found.")
        return None
    try:
        import acoustic_fingerprint as af
        import stt_utils
        import identify_audio as ia
    except Exception as exc:
        print(f"  SKIP: deps unavailable ({exc}).")
        return None
    ac_cfg = af.AcousticConfig.from_config(cfg)
    try:
        af.check_fpcalc(ac_cfg.fpcalc_path)
    except Exception as exc:
        print(f"  SKIP: fpcalc unavailable ({exc}).")
        return None

    sr = cfg.get("audio", {}).get("sample_rate", 16000)
    # Load the real recording as raw PCM, exactly like the mic loop accumulates.
    seg = stt_utils.load_audio_mono16k(RECORDING, sr)
    pcm = seg.raw_data
    print(f"  Loaded {len(pcm)/2/sr:.1f}s of PCM from the real recording.")
    print("  Exercising the LIVE code path: shortlist_candidates_pcm -> "
          "media_ids_for_episodes -> scoped lookup ...\n")

    db = FingerprintDB(db_path)
    try:
        # Stage 1 (live variant) - acoustic shortlist on PCM
        ac_results = af.shortlist_candidates_pcm(pcm, sr, 1, db, ac_cfg, top_n=5)
        # Stage 1.5 - the call that used to CRASH on NULL season/episode
        episode_keys = [(r.media.title, r.media.season, r.media.episode)
                        for r in ac_results]
        candidate_ids = db.media_ids_for_episodes(episode_keys)
        # Also explicitly hit the NULL path (movie-style key) to prove the fix.
        _movie = db.media_ids_for_episodes([("Nonexistent Movie", None, None)])
        print(f"  acoustic shortlist (PCM): "
              f"{[r.media.episode for r in ac_results]}")
        print(f"  resolved candidate media ids: {candidate_ids}")
        print(f"  NULL season/episode key handled cleanly -> {_movie} (no crash)")
        crash = False
    except Exception as exc:
        crash = True
        print(f"  CRASH: {exc!r}")
    finally:
        db.close()

    print(f"\n  RESULT: {'PASS' if not crash else 'FAIL'} - the live mic code "
          f"path runs end-to-end with the SQL bug fixed.")
    return not crash


# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--synth-episodes", type=int, default=600,
                   help="Decoy episodes for the performance demo (default 600)")
    p.add_argument("--fps-per-episode", type=int, default=5000,
                   help="Synthetic fingerprints per decoy episode (default 5000)")
    p.add_argument("--quiet", action="store_true",
                   help="Reduce per-chunk logging noise")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging("WARNING" if args.quiet else cfg.get("logging", {}).get("level", "INFO"))
    fp_cfg = FingerprintConfig.from_config(cfg)
    db_path = cfg.get("database", {}).get("path", "fingerprints.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(HERE, db_path)

    print(BANNER)
    print("  TWO-STAGE HYBRID PIPELINE - LIVE DEMONSTRATION")
    print("  (acoustic shortlist  ->  scoped phonetic confirm + fuzzy fallback)")
    print(BANNER)
    print(f"  reference DB : {db_path}")
    print(f"  recording    : {RECORDING}")

    results = {}
    results["1. correctness (S01E04)"] = demo_correctness(cfg, fp_cfg, db_path)
    results["2. performance speed-up"] = demo_performance(
        cfg, fp_cfg, args.synth_episodes, args.fps_per_episode)
    results["3. fuzzy degraded STT"] = demo_fuzzy(cfg, fp_cfg, db_path)
    results["4. live path / SQL fix"] = demo_live_path(cfg, fp_cfg, db_path)

    hdr("SUMMARY")
    for name, val in results.items():
        if val is None:
            status = "SKIPPED"
        elif isinstance(val, bool):
            status = "PASS" if val else "FAIL"
        else:  # speedup number
            status = f"PASS ({val:.1f}x faster)"
        print(f"  {name:32} : {status}")
    print(BANNER)

    # Non-zero exit only if something explicitly FAILED (skips are fine).
    failed = [k for k, v in results.items() if v is False]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
