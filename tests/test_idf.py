#!/usr/bin/env python3
"""Tests for IDF-style rarity weighting of reference-library shingle hashes.

Rare, episode-specific dialogue must count for more than filler shared across
every episode, the weighting must be computed lazily from an existing DB (no
rebuild), cached per database, and must never break scoring on tiny libraries.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import scoring  # noqa: E402
from engine.scoring import (  # noqa: E402
    apply_idf_weights,
    idf_weights,
    library_hash_idf,
)


def test_idf_weights_rank_rare_above_common():
    df = {"the": 100, "warehouse": 1, "jury": 2}
    w = idf_weights(df, n_docs=100)
    assert w["warehouse"] > w["jury"] > w["the"]


def test_apply_idf_weights_scales_in_place_and_is_noop_when_empty():
    weights = {"a": 1.0, "b": 0.6}
    # empty idf -> unchanged (exact previous behaviour)
    assert apply_idf_weights(dict(weights), {}) == weights
    out = apply_idf_weights(dict(weights), {"a": 2.0})
    assert out["a"] == 2.0  # 1.0 * 2.0
    assert out["b"] == 0.6  # untouched (no factor)


def test_library_hash_idf_lazy_cached_from_existing_db(ref_db, monkeypatch):
    """Factors come straight from the existing DB and are cached per path."""
    scoring._IDF_CACHE.clear()
    factors = library_hash_idf(ref_db)
    # The 2-episode fixture is below the IDF floor, so no factors are produced -
    # weighting silently degrades to plain time-weights (backward compatible).
    assert isinstance(factors, dict)

    # Force a library large enough to pass the floor and verify rare > common.
    df = {"common": 10, "rare": 1}
    monkeypatch.setattr(ref_db, "hash_document_frequencies",
                        lambda: (df, 10))
    scoring._IDF_CACHE.clear()
    factors = library_hash_idf(ref_db)
    assert factors, "expected non-empty factors for a 10-episode library"
    assert factors["rare"] > factors["common"]
    # Mean-normalised and clamped into the configured band.
    for f in factors.values():
        assert scoring._IDF_FACTOR_MIN <= f <= scoring._IDF_FACTOR_MAX

    # Second call is served from cache (same mtime) - underlying method not hit.
    def _boom():
        raise AssertionError("should have used the cache")
    monkeypatch.setattr(ref_db, "hash_document_frequencies", _boom)
    again = library_hash_idf(ref_db)
    assert again == factors


def test_library_hash_idf_never_raises_on_bad_db():
    class Broken:
        path = ""

        def hash_document_frequencies(self):
            raise RuntimeError("boom")

    scoring._IDF_CACHE.clear()
    assert library_hash_idf(Broken()) == {}


def test_rare_token_match_outscores_common_token_match():
    """End-to-end: with IDF weights, matching a rare shingle beats matching a
    common one even though both are a single-hash overlap."""
    from fingerprint_core import score_matches

    # Two candidate media rows: one shares a RARE hash, one a COMMON hash.
    class Row(dict):
        def keys(self):
            return super().keys()

        def __getitem__(self, k):
            return super().__getitem__(k)

    def mk(media_id, h):
        return Row(hash=h, shingle_size=3, start_ms=1000, end_ms=2000,
                   media_id=media_id, title="Matlock", year=1986,
                   media_type="tv", season=1, episode=media_id,
                   show_title="Matlock", episode_title=f"Ep{media_id}")

    query = ["rare", "common"]
    rows = [mk(1, "rare"), mk(2, "common")]
    idf = {"rare": 3.0, "common": 0.4}
    base = {"rare": 1.0, "common": 1.0}
    weights = apply_idf_weights(dict(base), idf)
    results = score_matches(query, rows, {}, query_weights=weights)
    top = results[0]
    assert top.media.episode == 1, "rare-token match should rank first"
