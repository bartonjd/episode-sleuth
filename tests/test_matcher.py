"""Tests for engine.matcher.identify_one.

Transcription (ffmpeg + Vosk) is mocked so these run fast and headless: we feed
identify_one a known transcript and assert it matches the correct episode in the
reference DB built from the subtitle fixtures.
"""
from types import SimpleNamespace

import pytest

import engine.matcher as matcher
from engine.matcher import identify_one


def _args(show_title="Matlock", review=0.40):
    """A stand-in for the argparse namespace identify_one expects."""
    return SimpleNamespace(
        points=[0.1, 0.3, 0.5, 0.7, 0.9],
        sample_len=15.0,
        review_confidence=review,
        runtime_tolerance=4.0,
        show_title=show_title,
    )


def _patch_transcription(monkeypatch, transcript, duration=1400.0):
    """Make identify_one see ``transcript`` without touching ffmpeg/Vosk."""
    # 5 windows across the runtime, all yielding the same known transcript.
    per_window = [(duration * f, transcript) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    monkeypatch.setattr(matcher, "_probe_duration", lambda path: duration)
    monkeypatch.setattr(matcher, "transcribe_samples",
                        lambda *a, **k: (per_window, len(per_window)))


def test_identify_one_with_matching_audio(monkeypatch, ref_db_path, engine_cfg,
                                          fp_cfg, sample_audio,
                                          ep1_noisy_transcript):
    """A clip whose dialogue matches EP1 is identified as S01E01, confidently."""
    _patch_transcription(monkeypatch, ep1_noisy_transcript)

    result = identify_one(sample_audio, ref_db_path, fp_cfg, engine_cfg,
                          _args(), transcriber=object(), runtimes=None)

    assert result.guess is not None
    assert result.guess.season == 1 and result.guess.episode == 1
    assert result.guess.episode_id == "S01E01"
    assert result.guess.mean_confidence > 0.5
    assert not result.needs_review


def test_identify_one_boost_reports_show_match(monkeypatch, ref_db_path,
                                               engine_cfg, fp_cfg, sample_audio,
                                               ep1_noisy_transcript):
    """When the batch show title matches, the notes record the +15% boost."""
    _patch_transcription(monkeypatch, ep1_noisy_transcript)
    result = identify_one(sample_audio, ref_db_path, fp_cfg, engine_cfg,
                          _args(show_title="Matlock"), transcriber=object(),
                          runtimes=None)
    assert result.guess is not None
    assert "show match" in result.notes


def test_identify_one_with_non_matching_audio(monkeypatch, ref_db_path,
                                              engine_cfg, fp_cfg, sample_audio,
                                              unrelated_transcript):
    """A clip with unrelated dialogue is either unmatched or flagged for review
    - never confidently mislabelled."""
    _patch_transcription(monkeypatch, unrelated_transcript)

    result = identify_one(sample_audio, ref_db_path, fp_cfg, engine_cfg,
                          _args(), transcriber=object(), runtimes=None)

    if result.guess is None:
        assert result.needs_review
    else:
        # A stray coincidental hit must not pass as a confident identification.
        assert result.needs_review or result.guess.mean_confidence < 0.40


def test_identify_one_no_transcriber(monkeypatch, ref_db_path, engine_cfg,
                                     fp_cfg, sample_audio):
    """With no STT engine available the file needs review and is not matched."""
    monkeypatch.setattr(matcher, "_probe_duration", lambda path: 1400.0)
    result = identify_one(sample_audio, ref_db_path, fp_cfg, engine_cfg,
                          _args(), transcriber=None, runtimes=None)
    assert result.guess is None
    assert result.needs_review
    assert "STT engine unavailable" in result.notes
