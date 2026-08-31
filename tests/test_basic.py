"""Basic smoke tests for the audio-fingerprint package.

These are intentionally lightweight so the suite runs without a Vosk model,
ffmpeg, or a populated database. They verify that packaging metadata and the
core (dependency-light) modules import correctly.
"""
import os
import sys

# Make sure the project root is importable when running pytest from anywhere.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_trivial():
    """A trivial always-passing test so pytest has something to collect."""
    assert True


def test_version_string():
    """The package exposes a version string."""
    import __init__ as pkg  # flat-layout package module (project root)
    assert isinstance(pkg.__version__, str)
    assert pkg.__version__.count(".") >= 1


def test_core_imports():
    """Dependency-light core modules import without side effects."""
    import fingerprint_core
    import subtitle_utils

    # A couple of key symbols exist.
    assert hasattr(fingerprint_core, "score_matches")
    assert hasattr(subtitle_utils, "parse_episode_info")


def test_parse_episode_info():
    """The subtitle episode parser handles a standard SxxExx filename."""
    from subtitle_utils import parse_episode_info

    season, episode, _title = parse_episode_info("Matlock (1986) - S01E03 - The Judge")
    assert season == 1
    assert episode == 3
