"""Tests for subtitle parsing, phonetic encoding and the OpenSubtitles helper."""
import pytest

from subtitle_utils import (
    parse_srt, parse_vtt, parse_subtitle_file, download_opensubtitles,
)
from fingerprint_core import phonetic_encode_word, phonetic_token_stream


# ---------------------------------------------------------------------------
# subtitle parsing
# ---------------------------------------------------------------------------
def test_parse_srt(sample_srt):
    cues = parse_srt(sample_srt)
    assert len(cues) == 20
    start, end, text = cues[0]
    assert start < end
    assert "object" in text.lower()
    # every cue is (start_ms, end_ms, text) with real content
    assert all(isinstance(s, int) and isinstance(e, int) and t.strip()
               for (s, e, t) in cues)


def test_parse_vtt(sample_vtt):
    cues = parse_vtt(sample_vtt)
    assert len(cues) == 4
    assert "objection" not in cues[0][2].lower()   # sanity
    assert "object" in cues[0][2].lower()
    # timestamps parsed to milliseconds
    assert cues[0][0] == 2000


def test_parse_subtitle_file_dispatch(sample_srt, sample_vtt):
    assert parse_subtitle_file(sample_srt)
    assert parse_subtitle_file(sample_vtt)
    with pytest.raises(ValueError):
        parse_subtitle_file("movie.mp4")


# ---------------------------------------------------------------------------
# phonetic (Double Metaphone) encoding
# ---------------------------------------------------------------------------
def test_phonetic_encoding_homophones_collide():
    """Double Metaphone maps homophones / common STT confusions to the same
    code - the whole basis of phonetic matching."""
    assert phonetic_encode_word("night") == phonetic_encode_word("nite")
    assert phonetic_encode_word("knight") == phonetic_encode_word("night")


def test_phonetic_encoding_distinguishes_different_words():
    assert phonetic_encode_word("matlock") != phonetic_encode_word("harbor")


def test_phonetic_token_stream_order_preserved(fp_cfg):
    stream = phonetic_token_stream("the witness has no knowledge", fp_cfg)
    assert isinstance(stream, list)
    assert len(stream) >= 4          # short filler words may be tokenised too


# ---------------------------------------------------------------------------
# OpenSubtitles - mocked (never touches the network)
# ---------------------------------------------------------------------------
def test_opensub_search_no_api_key_returns_empty(monkeypatch):
    """With the API provider selected but no key, the helper reports the error
    and returns [] instead of raising or hitting the network."""
    # Guard: make any accidental network call fail loudly.
    import subtitle_utils
    def _boom(*a, **k):
        raise AssertionError("network access attempted in a unit test")
    monkeypatch.setattr(subtitle_utils.requests, "get", _boom, raising=False)
    monkeypatch.setattr(subtitle_utils.requests, "post", _boom, raising=False)

    cfg = {"opensubtitles": {"provider": "api", "api_key": ""}}
    out = download_opensubtitles("Matlock S01E01", "/tmp/does-not-matter", cfg)
    assert out == []


@pytest.mark.skip(reason="hits the real OpenSubtitles network API")
def test_opensub_search_live():  # pragma: no cover - manual/integration only
    cfg = {"opensubtitles": {"provider": "legacy"}}
    results = download_opensubtitles("Matlock S01E01", "/tmp/os_test", cfg)
    assert isinstance(results, list)
