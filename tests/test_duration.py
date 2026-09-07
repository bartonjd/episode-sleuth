"""Tests for the episode-duration validation feature.

Covers, all offline:
  * the SQLite schema round-trips ``duration_seconds`` / ``duration_source``
    and migrates an older DB in place;
  * ``subtitle_duration_fallback`` derives seconds from the last cue;
  * the matcher only *hard-flags* a duration mismatch for authoritative
    (TVMaze / manual) runtimes, not for the unreliable subtitle estimate.
"""
import sqlite3

import pytest

from fingerprint_core import FingerprintDB, MediaInfo
from engine.duration_lookup import subtitle_duration_fallback


def _mk(**kw):
    base = dict(title="Matlock", media_type="tv", season=1, episode=1,
                source="s1e1.srt")
    base.update(kw)
    return MediaInfo(**base)


def test_schema_roundtrips_duration_and_source(tmp_path):
    db = FingerprintDB(str(tmp_path / "t.db"))
    mid = db.get_or_create_media(
        _mk(duration_seconds=2700, duration_source="tvmaze"))
    info = db.media_info(mid)
    assert info.duration_seconds == 2700
    assert info.duration_source == "tvmaze"


def test_update_media_can_set_manual_source(tmp_path):
    db = FingerprintDB(str(tmp_path / "t.db"))
    mid = db.get_or_create_media(_mk())
    assert db.update_media(mid, duration_seconds=1500,
                           duration_source="manual")
    info = db.media_info(mid)
    assert info.duration_seconds == 1500
    assert info.duration_source == "manual"


def test_migrates_legacy_db_without_duration_columns(tmp_path):
    """A pre-feature media table gains the new columns on open (NULL default)."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, year INTEGER, media_type TEXT,
            season INTEGER, episode INTEGER, source TEXT
        );
        INSERT INTO media (title, media_type, season, episode, source)
        VALUES ('Matlock', 'tv', 1, 1, 's1e1.srt');
        CREATE TABLE fingerprints (media_id INTEGER, hash TEXT, position INTEGER);
        CREATE TABLE media_tokens (media_id INTEGER, tokens TEXT, starts TEXT);
        """
    )
    conn.commit()
    conn.close()

    db = FingerprintDB(path)  # opening runs the in-place migration
    info = db.media_info(1)
    assert info is not None
    assert info.duration_seconds is None
    assert info.duration_source is None
    # ...and the migrated columns are writable.
    assert db.update_media(1, duration_seconds=1320, duration_source="tvmaze")
    assert db.media_info(1).duration_seconds == 1320


def test_subtitle_duration_fallback_uses_last_cue():
    cues = [(0, 2000, "hi"), (3000, 65000, "bye")]  # last cue ends at 65s
    assert subtitle_duration_fallback(cues) == 65


def test_subtitle_duration_fallback_empty_is_none():
    assert subtitle_duration_fallback([]) is None


# --- matcher source-gating -------------------------------------------------

def _identify(monkeypatch, ref_db_path, engine_cfg, fp_cfg, sample_audio,
              transcript, duration):
    import engine.matcher as matcher
    from engine.matcher import identify_one
    from types import SimpleNamespace
    per_window = [(duration * f, transcript) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    monkeypatch.setattr(matcher, "_probe_duration", lambda path: duration)
    monkeypatch.setattr(matcher, "transcribe_samples",
                        lambda *a, **k: (per_window, len(per_window)))
    args = SimpleNamespace(points=[0.1, 0.3, 0.5, 0.7, 0.9], sample_len=15.0,
                           review_confidence=0.40, runtime_tolerance=4.0,
                           show_title="Matlock")
    return identify_one(sample_audio, ref_db_path, fp_cfg, engine_cfg, args,
                        transcriber=object(), runtimes=None)


def _set_all_durations(db_path, seconds, source):
    db = FingerprintDB(db_path)
    for row in db.list_media():
        db.update_media(row["id"], duration_seconds=seconds,
                        duration_source=source)
    db.close()


def test_subtitle_source_mismatch_does_not_flag_review(
        monkeypatch, ref_db_path, engine_cfg, fp_cfg, sample_audio,
        ep1_noisy_transcript):
    """A wildly-off *subtitle-estimated* runtime must NOT force review."""
    _set_all_durations(ref_db_path, 120, "subtitle")  # 2m vs a ~23m clip
    result = _identify(monkeypatch, ref_db_path, engine_cfg, fp_cfg,
                       sample_audio, ep1_noisy_transcript, duration=1400.0)
    assert result.guess is not None
    assert not result.needs_review
    assert "duration mismatch" not in result.notes


def test_tvmaze_source_mismatch_flags_review(
        monkeypatch, ref_db_path, engine_cfg, fp_cfg, sample_audio,
        ep1_noisy_transcript):
    """A large mismatch against an authoritative TVMaze runtime flags review."""
    _set_all_durations(ref_db_path, 120, "tvmaze")  # 2m authoritative vs ~23m
    result = _identify(monkeypatch, ref_db_path, engine_cfg, fp_cfg,
                       sample_audio, ep1_noisy_transcript, duration=1400.0)
    assert result.guess is not None
    assert result.needs_review
    assert "duration mismatch" in result.notes


def test_matching_tvmaze_duration_no_flag(
        monkeypatch, ref_db_path, engine_cfg, fp_cfg, sample_audio,
        ep1_noisy_transcript):
    """A runtime within tolerance of the file duration does not flag review."""
    _set_all_durations(ref_db_path, 1400, "tvmaze")  # matches the ~23m clip
    result = _identify(monkeypatch, ref_db_path, engine_cfg, fp_cfg,
                       sample_audio, ep1_noisy_transcript, duration=1400.0)
    assert result.guess is not None
    assert "duration mismatch" not in result.notes
