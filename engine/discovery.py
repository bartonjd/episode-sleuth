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


def parse_media_exts(spec) -> set:
    """Turn a user-supplied extension list into a normalised set.

    Accepts a comma/space/semicolon separated string (e.g.
    ``"mp4, mkv;avi"`` or ``".MP4 .mkv"``) or an iterable of extensions.
    Returns a set of lower-case, dot-prefixed extensions
    (e.g. ``{".mp4", ".mkv", ".avi"}``). Falls back to the built-in
    ``MEDIA_EXTS`` when nothing usable is provided.
    """
    if not spec:
        return set(MEDIA_EXTS)
    if isinstance(spec, str):
        tokens = re.split(r"[,\s;]+", spec)
    else:
        tokens = list(spec)
    exts = set()
    for tok in tokens:
        tok = (tok or "").strip().lower()
        if not tok:
            continue
        if not tok.startswith("."):
            tok = "." + tok
        exts.add(tok)
    return exts or set(MEDIA_EXTS)


# Multi-part markers we treat as equivalent, e.g. "Part 1" == "(1)" == "Part I".
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
          "vii": 7, "viii": 8, "ix": 9, "x": 10}
_PART_RE = re.compile(
    r"""[\s\-_]*                     # leading separators
        \(?                          # optional opening paren
        (?:part|pt\.?|p)?\s*         # optional 'part'/'pt'/'p'
        (\d{1,2}|[ivx]{1,4})         # the part number (arabic or roman)
        \)?                          # optional closing paren
        \s*$                         # anchored to the end
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_part_markers(title: Optional[str]) -> str:
    """Collapse any trailing multi-part marker to a canonical ``part N``.

    ``"The Prisoner (1)"``, ``"The Prisoner Part 1"``, ``"The Prisoner - Part I"``
    and ``"The Prisoner (Part 1)"`` all normalise to ``"the prisoner part 1"``.
    Titles without a part marker are returned lower-cased and stripped.
    """
    if not title:
        return ""
    s = str(title).strip()
    m = _PART_RE.search(s)
    if m:
        tok = m.group(1).lower()
        num = _ROMAN.get(tok, None)
        if num is None:
            try:
                num = int(tok)
            except ValueError:
                num = tok
        base = s[:m.start()].strip(" -_")
        return f"{base} part {num}".strip().lower()
    return s.strip().lower()


def titles_equivalent(a: Optional[str], b: Optional[str],
                      ignore_part_format: bool = True) -> bool:
    """True when two episode titles refer to the same episode.

    When *ignore_part_format* is set (default), differing multi-part
    punctuation ("Part 1" vs "(1)") does not count as a difference.
    """
    na, nb = (a or "").strip().lower(), (b or "").strip().lower()
    if na == nb:
        return True
    if ignore_part_format:
        return normalize_part_markers(a) == normalize_part_markers(b)
    return False


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


def discover_media(path_dir: str, media_exts=None) -> List[str]:
    """List media files in *path_dir* whose extension is allowed.

    *media_exts* may be a set/iterable of extensions or a comma-separated
    string (see :func:`parse_media_exts`). Defaults to the built-in
    ``MEDIA_EXTS`` when not supplied.
    """
    exts = parse_media_exts(media_exts) if media_exts else set(MEDIA_EXTS)
    files = []
    for name in sorted(os.listdir(path_dir)):
        full = os.path.join(path_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in exts:
            files.append(full)
    return files
