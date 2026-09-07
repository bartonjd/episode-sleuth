#!/usr/bin/env python3
"""Episode runtime lookup helpers.

Two independent ways to learn how long a reference episode *should* be, used to
store an expected ``duration_seconds`` alongside each fingerprinted episode:

  * :func:`fetch_episode_runtime` - query the free, no-auth TVMaze API for the
    official runtime (minutes) of a specific season/episode.
  * :func:`subtitle_duration_fallback` - derive an approximate runtime from the
    timestamp of the last subtitle cue when the API is unavailable.

Everything here is deliberately *non-blocking* in spirit: every network call is
wrapped in a short timeout and a broad ``except`` so a slow or unreachable
TVMaze never breaks (or even slows down much) a library build. On any failure
the functions simply return ``None`` and the caller falls back gracefully.

TVMaze endpoints (no API key required):
    https://api.tvmaze.com/search/shows?q={show}
    https://api.tvmaze.com/shows/{id}/episodebynumber?season={s}&number={e}
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

# Default per-request network timeout (seconds). Kept short so a hung API never
# stalls a build for long; failures fall back to the subtitle heuristic.
DEFAULT_TIMEOUT = 8.0

_API_BASE = "https://api.tvmaze.com"

# Small in-process caches so a whole-season build hits the network at most once
# per show (search) instead of once per episode. Keyed by the normalised show
# name. A cached ``None`` means "already looked up, not found" so we do not
# retry a miss for every episode of the same show.
_show_id_cache: dict = {}
_runtime_cache: dict = {}


def _http_get_json(url: str, timeout: float):
    """GET ``url`` and parse JSON, or return ``None`` on any error.

    A ``User-Agent`` is set because some CDNs reject the default urllib agent.
    All network / decode / HTTP errors are swallowed and logged at debug level -
    duration lookup is strictly best-effort and must never raise.
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EpisodeSleuth/1.0 (+duration-lookup)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
        return json.loads(data.decode("utf-8", "ignore"))
    except Exception as exc:  # pragma: no cover - network dependent
        logging.debug("TVMaze request failed for %s: %s", url, exc)
        return None


def _normalise_show_name(show_name: str) -> str:
    """Strip a trailing ``(year)`` and surrounding whitespace for cache keys and
    a cleaner API query, e.g. ``"Matlock (1986)"`` -> ``"Matlock"``."""
    name = re.sub(r"\(\s*(19|20)\d{2}\s*\)", " ", show_name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name or show_name.strip()


def lookup_show_id(show_name: str, timeout: float = DEFAULT_TIMEOUT
                   ) -> Optional[int]:
    """Return the TVMaze show id for ``show_name`` (best match), or ``None``.

    Results (including misses) are cached per normalised show name so a whole
    season build performs at most one search request per show.
    """
    if not show_name:
        return None
    key = _normalise_show_name(show_name).lower()
    if key in _show_id_cache:
        return _show_id_cache[key]

    query = urllib.parse.quote(_normalise_show_name(show_name))
    url = f"{_API_BASE}/search/shows?q={query}"
    data = _http_get_json(url, timeout)
    show_id: Optional[int] = None
    if isinstance(data, list) and data:
        # The API returns candidates sorted by relevance; take the top hit.
        top = data[0]
        show = top.get("show") if isinstance(top, dict) else None
        if isinstance(show, dict):
            sid = show.get("id")
            if isinstance(sid, int):
                show_id = sid
    _show_id_cache[key] = show_id
    if show_id is None:
        logging.debug("TVMaze: no show match for %r", show_name)
    return show_id


def fetch_episode_runtime(show_name: str, season: Optional[int],
                          episode: Optional[int],
                          timeout: float = DEFAULT_TIMEOUT) -> Optional[int]:
    """Return the official episode runtime in **seconds**, or ``None``.

    Queries TVMaze for the show, then for the specific season/episode. Any
    missing input, network error, or absent runtime yields ``None`` so the
    caller can fall back to the subtitle heuristic. Never raises.
    """
    if not show_name or season is None or episode is None:
        return None
    cache_key = (_normalise_show_name(show_name).lower(), int(season),
                 int(episode))
    if cache_key in _runtime_cache:
        return _runtime_cache[cache_key]

    result: Optional[int] = None
    show_id = lookup_show_id(show_name, timeout=timeout)
    if show_id is not None:
        url = (f"{_API_BASE}/shows/{show_id}/episodebynumber"
               f"?season={int(season)}&number={int(episode)}")
        data = _http_get_json(url, timeout)
        if isinstance(data, dict):
            runtime = data.get("runtime")
            if runtime is None:
                runtime = data.get("airtime")  # never a number; guarded below
            if isinstance(runtime, (int, float)) and runtime > 0:
                result = int(round(float(runtime) * 60.0))
    _runtime_cache[cache_key] = result
    if result is not None:
        logging.debug("TVMaze: %s S%sE%s runtime = %ss",
                      show_name, season, episode, result)
    return result


def subtitle_duration_fallback(cues: List[Tuple[int, int, str]]
                               ) -> Optional[int]:
    """Approximate an episode's runtime (seconds) from its subtitle cues.

    Uses the end timestamp of the last cue as a lower bound on the episode
    length. ``cues`` is the list of ``(start_ms, end_ms, text)`` tuples returned
    by :func:`subtitle_utils.parse_subtitle_file`. Returns ``None`` for empty or
    malformed input.
    """
    if not cues:
        return None
    try:
        last_end_ms = max(int(c[1]) for c in cues if c and c[1] is not None)
    except (ValueError, TypeError, IndexError):
        return None
    if last_end_ms <= 0:
        return None
    return int(round(last_end_ms / 1000.0))
