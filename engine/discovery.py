#!/usr/bin/env python3
"""Media discovery, filename parsing and Plex-name construction.

Everything here is about *files and names*: enumerating media in a folder,
turning season/episode numbers into an id string, and composing the DB-correct
filename a rip should be renamed to. The season/episode/title parser itself
lives in ``subtitle_utils`` (it is shared with the fingerprint builder); it is
re-exported here so engine consumers have a single import surface.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

from .types import MEDIA_EXTS

# Re-export the filename parser so callers can do
# ``from engine.discovery import parse_episode_info``. The canonical
# implementation stays in subtitle_utils (shared with create_fingerprint).
try:  # pragma: no cover - defensive; subtitle_utils has light deps
    from subtitle_utils import parse_episode_info, clean_subtitle_filename
except Exception:  # pragma: no cover
    parse_episode_info = None          # type: ignore[assignment]
    clean_subtitle_filename = None     # type: ignore[assignment]


def episode_id_str(season: Optional[int], episode: Optional[int]) -> str:
    if season is not None and episode is not None:
        return f"S{season:02d}E{episode:02d}"
    if episode is not None:
        return f"E{episode:02d}"
    return "movie"


# Characters that are illegal in Windows filenames.
_ILLEGAL_FN_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """Strip characters that Windows forbids in filenames and tidy whitespace."""
    cleaned = _ILLEGAL_FN_RE.sub(" ", name or "")
    # collapse runs of whitespace and trim trailing dots/spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned


def build_suggested_filename(show: str, season: Optional[int],
                             episode: Optional[int], episode_title: str,
                             ext: str) -> str:
    """Compose the DB-correct filename for a media file:

        "<Show> - S05E02 - <Episode Title><ext>"

    Falls back gracefully when the episode title or season/episode are missing.
    """
    show = (show or "").strip()
    episode_title = (episode_title or "").strip()
    se = episode_id_str(season, episode)
    parts = [p for p in (show, se if se != "movie" else "", episode_title) if p]
    stem = " - ".join(parts) if parts else "Unknown"
    return sanitize_filename(stem) + (ext or "")


def discover_media(path_dir: str) -> List[str]:
    files = []
    for name in sorted(os.listdir(path_dir)):
        full = os.path.join(path_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in MEDIA_EXTS:
            files.append(full)
    return files
