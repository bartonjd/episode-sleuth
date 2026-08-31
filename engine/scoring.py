#!/usr/bin/env python3
"""Confidence scoring: time weighting, metadata boosts and the fuzzy fallback.

The raw shingle-overlap scorer, ``score_matches`` (and the fuzzy LCS scorer,
``score_fuzzy_matches``), live in ``fingerprint_core`` because they operate
directly on the database's fingerprint representation. They are re-exported here
so the engine has a single "scoring" import surface:

    from engine.scoring import score_matches, run_fuzzy_stage

This module owns the identification-side scoring logic that sits *on top* of the
raw scorer: per-sample time weighting, show / episode-title metadata boosts, the
adaptive review threshold and the order-preserving fuzzy fallback stage.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Dict, List, Optional, Tuple

from fingerprint_core import (
    FingerprintDB, FingerprintConfig, FuzzyConfig,
    fingerprint_text, phonetic_token_stream, score_fuzzy_matches, score_matches,
)

__all__ = [
    "score_matches", "score_fuzzy_matches",
    "run_fuzzy_stage", "_load_candidate_streams",
    "_time_weight", "_build_weighted_query", "_norm_title",
    "apply_metadata_boosts", "_adaptive_review_threshold",
]


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
# Confidence enhancement helpers
# ---------------------------------------------------------------------------
def _time_weight(fraction: float) -> float:
    """Weight a sample by WHERE in the runtime it was taken.

    Dialogue from the informative middle of an episode (roughly 20%-80% of the
    runtime) is the strongest identity signal; the opening and closing minutes
    are dominated by theme music, credits and recurring boilerplate that match
    many episodes. Samples in [0.2, 0.8] get full weight (1.0) and weight falls
    off linearly to 0.6 at the very start / end.
    """
    if 0.2 <= fraction <= 0.8:
        return 1.0
    if fraction < 0.2:
        return 0.6 + (fraction / 0.2) * 0.4
    return 0.6 + ((1.0 - fraction) / 0.2) * 0.4


def _build_weighted_query(per_window: List[Tuple[float, str]], duration: float,
                          fp_cfg: FingerprintConfig
                          ) -> Tuple[List[str], Dict[str, float]]:
    """Fingerprint each sample window separately and assign every shingle the
    time-weight of the window it came from. Returns ``(all_hashes, weights)``
    where ``weights`` maps hash -> max time-weight seen for that hash."""
    all_hashes: List[str] = []
    weights: Dict[str, float] = {}
    for (start_s, wtext) in per_window:
        frac = (start_s / duration) if duration > 0 else 0.5
        w = _time_weight(frac)
        for (h, _s) in fingerprint_text(wtext, fp_cfg):
            all_hashes.append(h)
            if w > weights.get(h, 0.0):
                weights[h] = w
    return all_hashes, weights


def _norm_title(s: Optional[str]) -> str:
    """Normalise a title for tolerant comparison (lowercase, alnum words)."""
    if not s:
        return ""
    s = re.sub(r"\(\d{4}\)", " ", s)          # drop a year in parentheses
    s = re.sub(r"[^0-9a-zA-Z]+", " ", s.lower())
    return " ".join(s.split()).strip()


def apply_metadata_boosts(results, expected_show: Optional[str],
                          query_episode_title: Optional[str]) -> List[str]:
    """Boost candidate confidences using show / episode-title metadata, in place.

      * show title exact match (candidate.show_title == expected_show): +0.15
      * episode title fuzzy match vs the query filename's parsed title: up to
        +0.10 scaled by similarity (>= 0.6 similarity required)

    Results are re-sorted by the boosted confidence. Returns a short list of
    human-readable boost notes for the winning candidate.
    """
    exp_show = _norm_title(expected_show)
    q_ep = _norm_title(query_episode_title)

    winner_notes: List[str] = []
    for r in results:
        notes: List[str] = []
        conf = r.confidence
        cand_show = _norm_title(r.media.show_title)
        if exp_show and cand_show and exp_show == cand_show:
            conf = min(1.0, conf + 0.15)
            notes.append("show match +15%")
        cand_ep = _norm_title(r.media.episode_title)
        if q_ep and cand_ep:
            ratio = difflib.SequenceMatcher(None, q_ep, cand_ep).ratio()
            if ratio >= 0.6:
                add = 0.10 * ratio
                conf = min(1.0, conf + add)
                notes.append(f"episode title +{add * 100:.0f}%")
        r.confidence = conf
        r._boost_notes = notes  # type: ignore[attr-defined]

    results.sort(key=lambda r: (r.confidence, r.match_count), reverse=True)
    if results:
        winner_notes = getattr(results[0], "_boost_notes", [])
    return winner_notes


def _adaptive_review_threshold(base: float, match_count: int,
                               boosted: bool) -> float:
    """Lower the review threshold when the evidence is strong.

    A match backed by many shingles (long sustained dialogue overlap) or by a
    confirmed show/episode-title match is trustworthy at a lower raw confidence
    than a thin, unsupported match, so we relax the manual-review threshold
    accordingly (never above the caller's configured value)."""
    thr = base
    if match_count >= 15:
        thr = base * 0.70
    elif match_count >= 8:
        thr = base * 0.85
    if boosted:
        thr *= 0.85
    return thr
