#!/usr/bin/env python3
"""audio_fingerprint.engine - the core dialogue-matching engine.

This package holds the reusable identification logic, cleanly separated from the
CLI wrapper (``audio_fingerprint.cli``) and the desktop GUI
(``audio_fingerprint.gui``). Import the public API directly from here:

    from engine import FileResult, identify_one, batch_identify, discover_media

Module map
----------
    types.py      FileResult / EpisodeGuess dataclasses and media constants
    discovery.py  discover_media, episode-id / Plex-name helpers
    scoring.py    time weighting, metadata boosts, fuzzy fallback
                  (re-exports score_matches from fingerprint_core)
    matcher.py    identify_one - identify a single file end to end
    batch.py      batch_identify - run identify_one over many files + writers

The low-level fingerprint representation and the raw shingle/LCS scorers live in
``fingerprint_core``; this package layers the identification workflow on top.
"""
from __future__ import annotations

import os
import sys

# Allow bare engine imports ("from fingerprint_core import ...") to resolve when
# this package is imported as ``audio_fingerprint.engine`` from elsewhere: the
# flat engine modules live in the project root (the parent of this directory).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .types import (
    FileResult, EpisodeGuess,
    MEDIA_EXTS, VIDEO_EXTS, AUDIO_EXTS, DEFAULT_SAMPLE_POINTS,
)
from .discovery import (
    discover_media, episode_id_str, build_suggested_filename, sanitize_filename,
)
from .scoring import score_matches, run_fuzzy_stage, apply_metadata_boosts
from .matcher import identify_one, transcribe_samples, sample_windows
from .batch import batch_identify, write_csv, write_json

__all__ = [
    # types
    "FileResult", "EpisodeGuess",
    "MEDIA_EXTS", "VIDEO_EXTS", "AUDIO_EXTS", "DEFAULT_SAMPLE_POINTS",
    # discovery
    "discover_media", "episode_id_str", "build_suggested_filename",
    "sanitize_filename",
    # scoring
    "score_matches", "run_fuzzy_stage", "apply_metadata_boosts",
    # matcher
    "identify_one", "transcribe_samples", "sample_windows",
    # batch
    "batch_identify", "write_csv", "write_json",
]
