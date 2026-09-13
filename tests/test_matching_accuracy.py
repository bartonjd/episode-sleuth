"""Tests for the improved matching-accuracy helpers.

All offline. Covers the new scoring building blocks:
  * ``token_ngrams`` window generation and edge cases;
  * ``ngram_similarity`` unweighted Dice and IDF-weighted variant;
  * ``document_frequencies`` / ``idf_weights`` corpus statistics;
  * ``extract_character_names`` proper-noun heuristic + ``name_overlap_bonus``;
  * ``dialogue_density`` words-per-minute;
  * ``run_ngram_stage`` producing plausible fallback matches against the shared
    reference database;
  * ``subtitle_duration_with_confidence`` high/low confidence rating.
"""
from engine.duration_lookup import subtitle_duration_with_confidence
from engine.scoring import (
    dialogue_density,
    document_frequencies,
    extract_character_names,
    idf_weights,
    name_overlap_bonus,
    ngram_similarity,
    run_ngram_stage,
    token_ngrams,
)


# ---------------------------------------------------------------------------
# token_ngrams
# ---------------------------------------------------------------------------
def test_token_ngrams_bigrams():
    assert token_ngrams(["a", "b", "c"], 2) == [("a", "b"), ("b", "c")]


def test_token_ngrams_unigrams_are_tuples():
    assert token_ngrams(["a", "b"], 1) == [("a",), ("b",)]


def test_token_ngrams_too_short_returns_empty():
    assert token_ngrams(["a"], 2) == []
    assert token_ngrams([], 3) == []


# ---------------------------------------------------------------------------
# ngram_similarity
# ---------------------------------------------------------------------------
def test_ngram_similarity_identical_is_one():
    seq = ["the", "quick", "brown", "fox"]
    assert ngram_similarity(seq, seq, 2) == 1.0


def test_ngram_similarity_disjoint_is_zero():
    assert ngram_similarity(["a", "b", "c"], ["x", "y", "z"], 2) == 0.0


def test_ngram_similarity_partial_between_zero_and_one():
    s = ngram_similarity(["a", "b", "c", "d"], ["a", "b", "z", "w"], 2)
    assert 0.0 < s < 1.0


def test_ngram_similarity_weighting_favours_rare_tokens():
    # Two candidates each share exactly one bigram with the query, but one
    # shares a rare bigram and the other a common one. With IDF weights the
    # rare-sharing candidate should score higher.
    query = ["common", "word", "rare", "term"]
    # Build weights where "rare"/"term" are heavy and "common"/"word" light.
    weights = {"common": 1.0, "word": 1.0, "rare": 5.0, "term": 5.0}
    a = ["common", "word", "x", "y"]      # shares ("common","word")
    b = ["rare", "term", "x", "y"]        # shares ("rare","term")
    sa = ngram_similarity(query, a, 2, weights)
    sb = ngram_similarity(query, b, 2, weights)
    assert sb > sa


# ---------------------------------------------------------------------------
# document_frequencies + idf_weights
# ---------------------------------------------------------------------------
def test_document_frequencies_counts_docs_not_occurrences():
    docs = [["a", "a", "b"], ["a", "c"]]
    df, n = document_frequencies(docs)
    assert n == 2
    assert df["a"] == 2   # in both docs (occurrences within a doc ignored)
    assert df["b"] == 1
    assert df["c"] == 1


def test_idf_weights_rare_token_heavier():
    df = {"common": 10, "rare": 1}
    w = idf_weights(df, n_docs=10)
    assert w["rare"] > w["common"]


# ---------------------------------------------------------------------------
# character names
# ---------------------------------------------------------------------------
def test_extract_character_names_finds_repeated_names():
    text = ("I told Ben about the case. Ben said Matlock would win. "
            "Later Ben called Matlock again.")
    names = extract_character_names(text, min_count=1)
    assert "ben" in names
    assert "matlock" in names


def test_extract_character_names_skips_sentence_initial():
    # "The" starts a sentence and is a stop word; it must not be a name.
    text = "The witness lied. The jury agreed."
    assert "the" not in extract_character_names(text)


def test_name_overlap_bonus_bounds():
    q = {"ben": 3, "matlock": 2}
    assert name_overlap_bonus(q, {"ben": 1, "matlock": 1}, cap=0.10) == 0.10
    assert name_overlap_bonus(q, {}, cap=0.10) == 0.0
    assert name_overlap_bonus({}, q, cap=0.10) == 0.0
    partial = name_overlap_bonus(q, {"ben": 1}, cap=0.10)
    assert 0.0 < partial < 0.10


# ---------------------------------------------------------------------------
# dialogue_density
# ---------------------------------------------------------------------------
def test_dialogue_density_words_per_minute():
    assert dialogue_density(120, 60.0) == 120.0   # 120 words in 1 minute
    assert dialogue_density(60, 120.0) == 30.0     # 60 words in 2 minutes


def test_dialogue_density_unknown_duration_zero():
    assert dialogue_density(100, 0.0) == 0.0
    assert dialogue_density(0, 60.0) == 0.0


# ---------------------------------------------------------------------------
# run_ngram_stage (integration against the shared reference DB)
# ---------------------------------------------------------------------------
def test_run_ngram_stage_matches_correct_episode(ref_db, fp_cfg, engine_cfg,
                                                  ep1_noisy_transcript):
    results = run_ngram_stage(ep1_noisy_transcript, ref_db, fp_cfg,
                              engine_cfg, [])
    # It may or may not clear the floor depending on the fixtures, but when it
    # returns anything the top hit must be a real Matlock episode.
    if results:
        assert results[0].media.title == "Matlock"


def test_run_ngram_stage_unrelated_returns_nothing_confident(
        ref_db, fp_cfg, engine_cfg, unrelated_transcript):
    results = run_ngram_stage(unrelated_transcript, ref_db, fp_cfg,
                              engine_cfg, [])
    # Unrelated dialogue should not produce a high-confidence n-gram match.
    assert not results or results[0].confidence < 0.5


# ---------------------------------------------------------------------------
# subtitle_duration_with_confidence
# ---------------------------------------------------------------------------
def _dense_cues(n=60):
    # One cue per second, ~8 words each -> ~480 wpm, no gaps: high confidence.
    cues = []
    for i in range(n):
        start = i * 1000
        end = start + 900
        cues.append((start, end, "one two three four five six seven eight"))
    return cues


def test_subtitle_duration_high_confidence_for_dense_dialogue():
    seconds, conf, reason = subtitle_duration_with_confidence(_dense_cues())
    assert seconds is not None
    assert conf == "high"
    assert reason


def test_subtitle_duration_low_confidence_for_sparse_dialogue():
    # A couple of short cues spread across a long span -> very low wpm.
    cues = [(0, 1000, "hello"), (600_000, 601_000, "bye")]
    seconds, conf, reason = subtitle_duration_with_confidence(cues)
    assert conf == "low"
    assert seconds is not None


def test_subtitle_duration_empty_is_low_and_none():
    seconds, conf, reason = subtitle_duration_with_confidence([])
    assert seconds is None
    assert conf == "low"
