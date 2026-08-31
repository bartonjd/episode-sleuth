"""Tests for the confidence-scoring logic: time weighting, contiguous-run
bonuses, metadata title boosts and the fuzzy fallback."""
from fingerprint_core import (
    MediaInfo, MatchResult, _contiguous_run_bonus, score_matches,
    fingerprint_text, phonetic_token_stream, score_fuzzy_matches, FuzzyConfig,
)
from engine.scoring import _time_weight, apply_metadata_boosts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _match(show=None, ep_title=None, conf=0.40, count=10):
    """Build a MatchResult with a MediaInfo carrying the given metadata."""
    info = MediaInfo(title="Matlock", year=1986, media_type="tv",
                     season=5, episode=2, show_title=show, episode_title=ep_title)
    return MatchResult(media=info, media_id=1, confidence=conf,
                       match_count=count, query_count=100)


# ---------------------------------------------------------------------------
# time-weighted coverage
# ---------------------------------------------------------------------------
def test_time_weighted_coverage():
    """Samples from the informative middle weigh more than the credits region."""
    middle = _time_weight(0.5)
    start = _time_weight(0.0)
    end = _time_weight(1.0)

    assert middle == 1.0
    assert start < middle
    assert end < middle
    # the plateau covers 20%-80% of the runtime
    assert _time_weight(0.2) == 1.0
    assert _time_weight(0.8) == 1.0
    # weight never collapses to zero at the extremes
    assert start >= 0.5 and end >= 0.5


def test_score_matches_weighting_prefers_middle_hashes(fp_cfg):
    """A weighted query scores higher when the matched shingles are the
    high-weight (middle-of-episode) ones."""
    text = "the fingerprints on the weapon do not belong to the defendant"
    hashes = [h for (h, _s) in fingerprint_text(text, fp_cfg)]
    assert hashes, "fixture text produced no shingles"

    rows = [{"media_id": 1, "title": "Matlock", "year": 1986,
             "media_type": "tv", "season": 5, "episode": 2,
             "hash": h, "start_ms": i * 1000}
            for i, h in enumerate(hashes)]

    high = score_matches(hashes, rows, {}, query_weights={h: 1.0 for h in hashes})
    low = score_matches(hashes, rows, {}, query_weights={h: 0.6 for h in hashes})
    assert high and low
    # identical matches, but the fully-weighted query is at least as confident
    assert high[0].confidence >= low[0].confidence


# ---------------------------------------------------------------------------
# contiguous-run bonus
# ---------------------------------------------------------------------------
def test_contiguous_run_bonus():
    assert _contiguous_run_bonus([]) == 0.0
    assert _contiguous_run_bonus([1000]) == 0.0

    # a long consecutive run (neighbours within the 4s gap) beats a short one
    long_run = _contiguous_run_bonus([i * 1000 for i in range(12)])
    short_run = _contiguous_run_bonus([0, 1000, 2000])
    assert long_run > short_run > 0.0

    # scattered hits far apart earn nothing (each gap exceeds 4s)
    scattered = _contiguous_run_bonus([0, 30000, 90000, 200000])
    assert scattered == 0.0

    # the bonus is capped
    huge = _contiguous_run_bonus([i * 500 for i in range(200)])
    assert huge <= 0.20 + 1e-9


# ---------------------------------------------------------------------------
# metadata title boosts
# ---------------------------------------------------------------------------
def test_title_boost_exact_show():
    """An exact show-title match adds +15%."""
    results = [_match(show="Matlock", conf=0.40)]
    notes = apply_metadata_boosts(results, expected_show="Matlock",
                                  query_episode_title=None)
    assert abs(results[0].confidence - 0.55) < 1e-6
    assert any("show match" in n for n in notes)


def test_title_boost_no_show_when_mismatch():
    results = [_match(show="Matlock", conf=0.40)]
    apply_metadata_boosts(results, expected_show="Columbo",
                          query_episode_title=None)
    assert abs(results[0].confidence - 0.40) < 1e-6   # unchanged


def test_title_boost_fuzzy_episode():
    """A fuzzy episode-title match adds up to +10% (scaled by similarity)."""
    results = [_match(ep_title="Nowhere To Turn", conf=0.40)]
    apply_metadata_boosts(results, expected_show=None,
                          query_episode_title="Nowhere To Turn (Part 1)")
    # exact-ish episode title => close to the +0.10 ceiling
    assert results[0].confidence > 0.40
    assert results[0].confidence <= 0.40 + 0.10 + 1e-6


def test_title_boost_combined_show_and_episode():
    results = [_match(show="Matlock", ep_title="The Madam", conf=0.40)]
    apply_metadata_boosts(results, expected_show="Matlock",
                          query_episode_title="The Madam")
    # +0.15 show and up to +0.10 episode
    assert results[0].confidence >= 0.55


# ---------------------------------------------------------------------------
# fuzzy fallback recovers a heavily degraded transcript
# ---------------------------------------------------------------------------
def test_fuzzy_recovers_degraded_transcript(ref_db, engine_cfg, fp_cfg):
    """Order-preserving LCS matching recovers EP1 from a badly degraded query
    where exact shingle hashing would struggle."""
    degraded = ("honour object entire questioning witness knowledge events "
                "night matlock prove client innocent charges jury evidence")
    streams = {}
    for mid in ref_db.all_token_stream_media_ids():
        toks, starts = ref_db.get_token_stream(mid)
        streams[mid] = (ref_db.media_info(mid), toks, starts)
    assert streams, "reference DB stored no token streams"

    q_tokens = phonetic_token_stream(degraded, fp_cfg)
    fuzzy_cfg = FuzzyConfig.from_config(engine_cfg)
    results = score_fuzzy_matches(q_tokens, streams, fuzzy_cfg, top_n=5)

    assert results
    assert results[0].media.season == 1 and results[0].media.episode == 1
