#!/usr/bin/env python3
"""Shared types and constants for the identification engine.

These are the dependency-light building blocks (dataclasses and extension
constants) used across the engine package. Keeping them in their own module
means every other engine module - and any external consumer - can import the
result types without pulling in ffmpeg, the database or the scorer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Video/audio containers we will try to identify.
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".mpg", ".mpeg",
              ".ts", ".wmv", ".flv", ".webm"}
# Plain audio files are accepted too (handy for testing / audio-only rips).
AUDIO_EXTS = {".m4a", ".wav", ".mp3", ".flac", ".aac", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

DEFAULT_SAMPLE_POINTS = [0.10, 0.30, 0.50, 0.70, 0.90]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------
@dataclass
class EpisodeGuess:
    """One candidate episode identity with the evidence behind it."""
    episode_id: str                 # e.g. "S01E04"  (or "movie" / "?")
    title: str                      # show title, e.g. "Matlock (1986)"
    season: Optional[int]
    episode: Optional[int]
    votes: int                      # samples that contributed (kept for compat)
    total_samples: int              # samples that produced any transcript
    mean_confidence: float          # phonetic match confidence
    method: str = "phonetic"        # phonetic | fuzzy
    episode_title: str = ""         # canonical episode title from the DB


@dataclass
class FileResult:
    filename: str
    path: str
    duration_s: float
    guess: Optional[EpisodeGuess]
    needs_review: bool
    notes: str = ""
    elapsed_s: float = 0.0
    # Naming verification (the whole point of the tool): is this file already
    # named the way the reference library says it should be, and if not, what
    # should it be renamed to?
    name_status: str = "unknown"    # correct | rename | unknown
    suggested_filename: str = ""

    def to_row(self) -> dict:
        g = self.guess
        return {
            "filename": self.filename,
            "episode_id": g.episode_id if g else "UNKNOWN",
            "title": g.title if g else "",
            "episode_title": g.episode_title if g else "",
            "confidence": round(g.mean_confidence, 4) if g else 0.0,
            "agreement": (f"{g.votes}/{g.total_samples}" if g else "0/0"),
            "method": g.method if g else "none",
            "name_status": self.name_status,
            "suggested_filename": self.suggested_filename,
            "duration_s": round(self.duration_s, 1),
            "needs_review": self.needs_review,
            "notes": self.notes,
            "elapsed_s": round(self.elapsed_s, 2),
        }
