#!/usr/bin/env python3
"""
selftest.py
===========
End-to-end test of the phonetic fingerprint pipeline that needs no STT model,
no microphone and no internet. It:

  1. writes a synthetic Matlock-style .srt subtitle file,
  2. fingerprints it into a temporary database,
  3. builds a "transcript" from the same dialogue but with deliberate
     speech-to-text style errors (homophones, misspellings, dropped words),
  4. matches that noisy transcript and asserts the correct episode wins.
"""

import os
import tempfile

from fingerprint_core import (
    load_config, FingerprintConfig, FingerprintDB, fingerprint_text, score_matches,
    phonetic_token_stream, score_fuzzy_matches, FuzzyConfig,
)
import create_fingerprint as cf

SRT_EP1 = """1
00:00:01,000 --> 00:00:04,000
Your Honor, I object to this entire line of questioning.

2
00:00:05,000 --> 00:00:08,000
The witness clearly has no knowledge of the events that night.

3
00:00:09,000 --> 00:00:12,000
Ben Matlock will prove my client is innocent of all charges.

4
00:00:13,000 --> 00:00:16,000
Ladies and gentlemen of the jury, consider the evidence carefully.
"""

SRT_EP2 = """1
00:00:01,000 --> 00:00:04,000
The defendant was seen near the harbor on the evening of the murder.

2
00:00:05,000 --> 00:00:08,000
We will demonstrate beyond reasonable doubt who is responsible.

3
00:00:09,000 --> 00:00:12,000
Detective, please describe what you found at the crime scene.
"""

# Noisy "STT transcript" of EP1 dialogue: homophones + misspellings + drops
NOISY_TRANSCRIPT = (
    "your honour i object to this entire line of questioning "
    "the witnes clearly has no knowledge of the events that nite "
    "ben matlock will proove my client is inocent of all charges "
    "ladies and gentlemen of the jurry consider the evidence carefuly"
)

# HEAVILY degraded transcript of EP1: ~40% of words dropped + mis-heard, plus
# some inserted filler words. Exact shingle hashing struggles badly here because
# nearly every 3/4/5-token shingle is broken by a gap; the order-preserving
# fuzzy matcher should still recover the correct episode.
FUZZY_TRANSCRIPT = (
    "honour object um entire questioning "
    "witness no knowledge events night "
    "uh matlock prove client innocent charges "
    "gentlemen jury evidence"
)


def main():
    tmpdir = tempfile.mkdtemp(prefix="fp_selftest_")
    db_path = os.path.join(tmpdir, "test.db")
    ep1 = os.path.join(tmpdir, "Matlock.1986.S01E01.srt")
    ep2 = os.path.join(tmpdir, "Matlock.1986.S01E02.srt")
    with open(ep1, "w") as fh:
        fh.write(SRT_EP1)
    with open(ep2, "w") as fh:
        fh.write(SRT_EP2)

    cfg = load_config()
    fp_cfg = FingerprintConfig.from_config(cfg)
    db = FingerprintDB(db_path)

    print("1) Fingerprinting synthetic subtitles ...")
    n1 = cf.fingerprint_subtitle_file(ep1, db, fp_cfg, "Matlock", 1986)
    n2 = cf.fingerprint_subtitle_file(ep2, db, fp_cfg, "Matlock", 1986)
    print(f"   EP1 fingerprints: {n1}, EP2 fingerprints: {n2}")
    print("   DB stats:", db.stats())

    print("\n2) Matching noisy STT-style transcript of EP1 dialogue ...")
    query = [h for (h, _s) in fingerprint_text(NOISY_TRANSCRIPT, fp_cfg)]
    rows = db.lookup(query)
    results = score_matches(query, rows, cfg.get("matching", {}))

    print("   Ranked results:")
    for r in results:
        print(f"     - {r.media.label():28} conf={r.confidence:.1%} hits={r.match_count}")

    assert results, "No results returned!"
    best = results[0]
    assert best.media.season == 1 and best.media.episode == 1, \
        f"Wrong episode matched: {best.media.label()}"
    assert best.confidence >= cfg["matching"]["confidence_threshold"], \
        "Confidence below threshold"

    print("\n   PASS: correct episode (S01E01) identified from noisy transcript.")
    print(f"   Best match: {best.media.label()} @ {best.confidence:.1%}")

    # ---- Step 3: fuzzy (order-preserving LCS) on a HEAVILY degraded query -----
    print("\n3) Fuzzy phonetic LCS match on heavily degraded transcript ...")
    # Build candidate streams exactly like the hybrid identifier does.
    streams = {}
    for mid in db.all_token_stream_media_ids():
        toks, starts = db.get_token_stream(mid)
        streams[mid] = (db.media_info(mid), toks, starts)
    assert streams, "No token streams were stored during fingerprinting!"

    q_tokens = phonetic_token_stream(FUZZY_TRANSCRIPT, fp_cfg)
    fuzzy_cfg = FuzzyConfig.from_config(cfg)
    fz = score_fuzzy_matches(q_tokens, streams, fuzzy_cfg, top_n=5)
    print("   Fuzzy ranked results:")
    for r in fz:
        print(f"     - {r.media.label():28} fuzzy={r.confidence:.1%} "
              f"LCS={r.match_count}/{r.query_count}")

    # Show that exact hashing is weaker on this heavily-degraded query.
    eq = [h for (h, _s) in fingerprint_text(FUZZY_TRANSCRIPT, fp_cfg)]
    exact = score_matches(eq, db.lookup(eq), cfg.get("matching", {}))
    exact_best = exact[0].confidence if exact else 0.0
    print(f"   (exact-hash best for same query: {exact_best:.1%})")

    assert fz, "Fuzzy matcher returned no results!"
    fbest = fz[0]
    assert fbest.media.season == 1 and fbest.media.episode == 1, \
        f"Fuzzy matched wrong episode: {fbest.media.label()}"
    assert fbest.confidence >= fuzzy_cfg.min_lcs_ratio, \
        f"Fuzzy confidence {fbest.confidence:.1%} below ratio {fuzzy_cfg.min_lcs_ratio:.0%}"
    print(f"\n   PASS: fuzzy recovered S01E01 @ {fbest.confidence:.1%} "
          f"(>= {fuzzy_cfg.min_lcs_ratio:.0%} ratio).")

    db.close()
    print("\nSELF-TEST PASSED ✔")


if __name__ == "__main__":
    main()
