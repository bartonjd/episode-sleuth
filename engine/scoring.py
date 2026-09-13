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
import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fingerprint_core import (
    FingerprintConfig,
    FingerprintDB,
    FuzzyConfig,
    MatchResult,
    fingerprint_text,
    phonetic_token_stream,
    score_fuzzy_matches,
    score_matches,
)

__all__ = [
    "score_matches", "score_fuzzy_matches",
    "run_fuzzy_stage", "run_ngram_stage", "_load_candidate_streams",
    "_time_weight", "_build_weighted_query", "_norm_title",
    "apply_metadata_boosts", "_adaptive_review_threshold",
    # Accuracy helpers (word frequency, n-grams, names, dialogue density)
    "token_ngrams", "ngram_similarity", "document_frequencies", "idf_weights",
    "extract_character_names", "name_overlap_bonus", "dialogue_density",
]


# ---------------------------------------------------------------------------
# Low-overhead accuracy helpers
#
# These sit alongside the phonetic scorer and provide independent, cheap signals
# used by the matcher's n-gram fallback and its tie-break logic. They are all
# pure functions with no I/O so they are trivially testable and never raise on
# ordinary input.
# ---------------------------------------------------------------------------

# A short stop list of high-frequency English function words. They carry almost
# no discriminating power between episodes, so they are ignored when extracting
# n-grams and character names (keeping the signal focused on rare, meaningful
# tokens). Kept intentionally small to stay language-neutral-ish.
_STOPWORDS = frozenset("""
a an the and or but if then else of to in on at by for with from into over
is are was were be been being am do does did have has had will would shall
should can could may might must i you he she it we they me him her us them my
your his its our their this that these those as so not no yes at up out off
""".split())


def token_ngrams(tokens: Sequence[str], n: int = 2) -> List[Tuple[str, ...]]:
    """Return the list of contiguous ``n``-grams over ``tokens``.

    Works on any token sequence (raw words or phonetic codes). For ``n`` larger
    than the input length an empty list is returned. Unigrams (``n == 1``) are
    returned as 1-tuples so all callers get a uniform shape.
    """
    if n < 1 or len(tokens) < n:
        return []
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def ngram_similarity(a: Sequence[str], b: Sequence[str], n: int = 2,
                     weights: Optional[Dict[str, float]] = None) -> float:
    """Weighted Dice similarity between the ``n``-gram *sets* of two sequences.

    Returns a value in ``[0.0, 1.0]``. When ``weights`` (a token -> weight map,
    e.g. IDF weights from :func:`idf_weights`) is supplied, each shared n-gram
    contributes the average weight of its member tokens, so rare/discriminative
    tokens dominate the score and boilerplate contributes little. Unweighted, it
    is the classic set Dice coefficient. Order-independent, so it complements the
    order-preserving LCS fuzzy scorer as a second opinion.
    """
    ga = set(token_ngrams(list(a), n))
    gb = set(token_ngrams(list(b), n))
    if not ga or not gb:
        return 0.0

    def _gram_weight(gram: Tuple[str, ...]) -> float:
        if not weights:
            return 1.0
        vals = [weights.get(t, 1.0) for t in gram]
        return sum(vals) / len(vals) if vals else 1.0

    shared = ga & gb
    inter = sum(_gram_weight(g) for g in shared)
    total = sum(_gram_weight(g) for g in ga) + sum(_gram_weight(g) for g in gb)
    if total <= 0:
        return 0.0
    return (2.0 * inter) / total


def document_frequencies(docs: Iterable[Sequence[str]]) -> Tuple[Dict[str, int], int]:
    """Return ``(token -> number of docs containing it, total doc count)``.

    Each document is a token sequence; a token is counted at most once per
    document. Used to derive corpus-wide rarity for :func:`idf_weights`.
    """
    df: Counter = Counter()
    n_docs = 0
    for doc in docs:
        n_docs += 1
        for tok in set(doc):
            df[tok] += 1
    return dict(df), n_docs


def idf_weights(df: Dict[str, int], n_docs: int) -> Dict[str, float]:
    """Smoothed inverse-document-frequency weight per token.

    ``weight = ln((1 + n_docs) / (1 + df)) + 1`` so a token appearing in every
    episode tends toward ~1.0 while a token unique to one episode gets the
    largest weight. Rare words are therefore the strongest match evidence, which
    is exactly what we want when disambiguating similar episodes.
    """
    weights: Dict[str, float] = {}
    for tok, count in df.items():
        weights[tok] = math.log((1.0 + n_docs) / (1.0 + count)) + 1.0
    return weights


# Capitalised word (a likely proper noun / character name). Applied to raw
# transcript text, NOT phonetic tokens (which have lost their casing).
_CAP_WORD_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")


def extract_character_names(text: str, min_count: int = 1) -> Dict[str, int]:
    """Extract likely character / proper-noun names from raw transcript text.

    Returns ``{name_lowercased: occurrence_count}``. Heuristic: capitalised
    words of 3+ letters that are not common stop words and not the first word of
    a sentence (sentence-initial capitals are usually ordinary words). Shows
    repeat character names constantly, so a strong overlap of names between a
    query transcript and a reference is a good same-episode signal.

    ``min_count`` filters out names seen fewer than this many times.
    """
    if not text:
        return {}
    counts: Counter = Counter()
    # Split into rough sentences so we can drop sentence-initial capitals.
    for sentence in re.split(r"[.!?]+", text):
        words = sentence.split()
        for idx, raw in enumerate(words):
            m = _CAP_WORD_RE.match(raw)
            if not m:
                continue
            if idx == 0:
                continue  # sentence-initial capital: usually not a name
            name = m.group(1).lower()
            if name in _STOPWORDS:
                continue
            counts[name] += 1
    return {k: v for k, v in counts.items() if v >= min_count}


def name_overlap_bonus(query_names: Dict[str, int],
                       ref_names: Dict[str, int], cap: float = 0.10) -> float:
    """Confidence bonus (0..``cap``) from shared character names.

    Scaled by the fraction of the query's names that also appear in the
    reference. Returns 0.0 when either side has no detected names, so it is a
    purely additive signal that never penalises.
    """
    if not query_names or not ref_names:
        return 0.0
    shared = set(query_names) & set(ref_names)
    if not shared:
        return 0.0
    frac = len(shared) / float(len(query_names))
    return min(cap, cap * frac)


def dialogue_density(word_count: int, duration_seconds: float) -> float:
    """Words per minute of dialogue, or 0.0 when duration is unknown.

    A cheap secondary signal: two recordings of the same episode should have a
    similar spoken-word rate. It is only ever used to break near-ties, never to
    override the phonetic verdict.
    """
    if duration_seconds <= 0 or word_count <= 0:
        return 0.0
    return word_count / (duration_seconds / 60.0)


# ---------------------------------------------------------------------------
# Phonetic fuzzy fallback (self-contained; tolerant of STT word errors)
# ---------------------------------------------------------------------------
def _load_candidate_streams(db: FingerprintDB, media_ids: Iterable[int],
                            fp_cfg: FingerprintConfig) -> Dict[int, Tuple[Any, Any, Any]]:
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
                    fp_cfg: FingerprintConfig, cfg: dict,
                    candidate_ids: Optional[Iterable[int]]) -> Tuple[List[Any], FuzzyConfig]:
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


def run_ngram_stage(query_text: str, db: FingerprintDB,
                    fp_cfg: FingerprintConfig, cfg: dict,
                    candidate_ids: Optional[Iterable[int]] = None,
                    n: int = 2, min_similarity: float = 0.20,
                    min_margin: float = 0.05) -> List[Any]:
    """Last-resort word/token n-gram similarity fallback.

    Runs only when the exact and fuzzy stages have failed or are weak. Unlike the
    order-preserving LCS fuzzy scorer, this compares the *set* of phonetic-token
    n-grams (order-independent) with IDF weighting so rare, discriminative tokens
    dominate. This recovers matches where STT dropped or reordered words badly
    enough to defeat LCS, at the cost of precision, so it is gated behind a
    similarity floor and a winner-vs-runner-up margin and is reported as the
    weaker ``ngram`` method.

    Returns a list of :class:`MatchResult` (possibly empty), best first.
    """
    if not query_text or not query_text.strip():
        return []
    q_tokens = phonetic_token_stream(query_text, fp_cfg)
    if len(q_tokens) < max(n + 1, 4):
        return []

    scope = list(candidate_ids) if candidate_ids else []
    streams = _load_candidate_streams(db, scope, fp_cfg) if scope else {}
    if not streams:
        streams = _load_candidate_streams(
            db, db.all_token_stream_media_ids(), fp_cfg)
    if not streams:
        return []

    # IDF weights over the reference corpus so common tokens count for little.
    df, n_docs = document_frequencies(toks for (_i, toks, _s) in streams.values())
    weights = idf_weights(df, n_docs)

    scored: List[MatchResult] = []
    for mid, (info, ref_tokens, _starts) in streams.items():
        sim = ngram_similarity(q_tokens, ref_tokens, n=n, weights=weights)
        if sim <= 0:
            continue
        # Approximate the shared n-gram count for the caller's evidence display.
        shared = len(set(token_ngrams(q_tokens, n)) & set(token_ngrams(ref_tokens, n)))
        scored.append(MatchResult(
            media=info, media_id=mid, confidence=min(1.0, sim),
            match_count=shared, query_count=max(1, len(q_tokens) - n + 1)))

    if not scored:
        return []
    scored.sort(key=lambda r: (r.confidence, r.match_count), reverse=True)
    top_k = cfg.get("matching", {}).get("top_n_results", 5)
    scored = scored[:top_k]

    if scored[0].confidence < min_similarity:
        return []
    # Ambiguity gate: require a clear winner, mirroring the fuzzy stage.
    if len(scored) >= 2 and (scored[0].confidence - scored[1].confidence) < min_margin:
        logging.info("  ngram: ambiguous (%.0f%% vs %.0f%%) - rejecting",
                     scored[0].confidence * 100, scored[1].confidence * 100)
        return []
    return scored


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


def apply_metadata_boosts(results: List[Any], expected_show: Optional[str],
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
