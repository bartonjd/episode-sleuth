"""Tests for media discovery and filename parsing (engine.discovery +
subtitle_utils.parse_episode_info / clean_subtitle_filename)."""
import os

from engine.discovery import (
    discover_media, episode_id_str, sanitize_filename, build_suggested_filename,
)
from subtitle_utils import parse_episode_info, clean_subtitle_filename


# ---------------------------------------------------------------------------
# discover_media
# ---------------------------------------------------------------------------
def test_discover_media_finds_files(tmp_path):
    """discover_media returns only recognised media files, sorted, and skips
    non-media files."""
    for name in ["b.mp4", "a.mkv", "c.avi", "notes.txt", "cover.jpg"]:
        (tmp_path / name).write_bytes(b"x")

    found = discover_media(str(tmp_path))
    names = [os.path.basename(p) for p in found]

    assert names == ["a.mkv", "b.mp4", "c.avi"]          # sorted, media only
    assert all(os.path.isabs(p) for p in found)


def test_discover_media_empty_dir(tmp_path):
    assert discover_media(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# parse_episode_info - the formats the identifier must understand
# ---------------------------------------------------------------------------
def test_parse_episode_info_s01e03():
    season, episode, _title = parse_episode_info(
        "Matlock (1986) - S01E03 - The Judge.srt")
    assert (season, episode) == (1, 3)


def test_parse_episode_info_1x03():
    season, episode, _title = parse_episode_info("Matlock.1x03.The.Judge.mkv")
    assert (season, episode) == (1, 3)


def test_parse_episode_info_compact_103():
    """A bare compact "103" is read as S01E03."""
    season, episode, _title = parse_episode_info("Matlock 103.mp4")
    assert (season, episode) == (1, 3)


def test_parse_episode_info_multi():
    """Multi-episode "S01E01E02" keys on the FIRST episode number."""
    season, episode, _title = parse_episode_info(
        "Matlock (1986) - S01E01E02 - Diary of a Perfect Murder.srt")
    assert season == 1
    assert episode == 1


def test_parse_episode_info_extracts_title():
    _s, _e, title = parse_episode_info(
        "Matlock (1986) - S01E03 - The Judge.srt")
    assert title is not None
    assert "judge" in title.lower()


# ---------------------------------------------------------------------------
# clean_subtitle_filename - strip release junk
# ---------------------------------------------------------------------------
def test_clean_subtitle_filename_strips_junk():
    cleaned = clean_subtitle_filename(
        "Matlock.S01E03.DVDRip.x264.BluRay.AAC-GROUP").lower()
    for junk in ("dvdrip", "x264", "bluray", "aac"):
        assert junk not in cleaned
    # meaningful tokens survive
    assert "matlock" in cleaned


def test_clean_subtitle_filename_keeps_plain_name():
    cleaned = clean_subtitle_filename("Matlock - The Judge")
    assert "Matlock" in cleaned
    assert "Judge" in cleaned


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def test_episode_id_str():
    assert episode_id_str(1, 3) == "S01E03"
    assert episode_id_str(None, 5) == "E05"
    assert episode_id_str(None, None) == "movie"


def test_sanitize_filename_removes_illegal_chars():
    out = sanitize_filename('Matlock: The "Trial" <part 1>')
    for ch in '\\/:*?"<>|':
        assert ch not in out


def test_build_suggested_filename():
    name = build_suggested_filename("Matlock", 5, 2, "Nowhere To Turn", ".mp4")
    assert name == "Matlock - S05E02 - Nowhere To Turn.mp4"
