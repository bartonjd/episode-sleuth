"""
subtitle_utils.py
=================
Parsing of .srt / .vtt subtitle files and OpenSubtitles.org download helpers.

Each parsed cue is a (start_ms, end_ms, text) tuple.  Filename metadata such as
season/episode/year is heuristically extracted so the fingerprint database can
be labelled automatically.
"""

import os
import re
import gzip
import time
import logging
import zipfile
import tempfile
from typing import List, Tuple, Optional

import requests
import pysrt
import webvtt

from fingerprint_core import MediaInfo

Cue = Tuple[int, int, str]


# ---------------------------------------------------------------------------
# Filename metadata heuristics
# ---------------------------------------------------------------------------

_SE_PATTERNS = [
    # S01E02, and multi-episode S01E01E02 (the extra E-numbers are captured so a
    # range can be detected; we key the media on the FIRST episode number).
    re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?:[ ._-]?[Ee]\d{1,3})*"),
    re.compile(r"(\d{1,2})x(\d{2,3})"),                          # 1x02
    re.compile(r"[Ss]eason[ ._-]?(\d{1,2}).*?[Ee]pisode[ ._-]?(\d{1,3})", re.I),
]
_YEAR_RE = re.compile(r"(19|20)\d{2}")

# "103"-style compact SxxEyy where nothing else marks the boundary. Kept SEPARATE
# from the patterns above (and only tried as a last resort in parse_episode_info)
# because a bare 3-4 digit run is easily confused with a year or a resolution.
_COMPACT_SE_RE = re.compile(r"(?<!\d)(\d)(\d{2})(?!\d)")

# Release / rip junk tokens stripped by clean_subtitle_filename before any title
# text is recovered. Matched case-insensitively as whole tokens.
_JUNK_TOKENS = [
    "dvdrip", "dvd", "bdrip", "brrip", "bluray", "blu-ray", "webrip", "web-dl",
    "webdl", "web", "hdtv", "hdrip", "pdtv", "subrip", "srt", "vtt",
    "x264", "x265", "h264", "h265", "hevc", "xvid", "divx", "avc",
    "aac", "ac3", "dd5", "dd5.1", "dts", "mp3", "flac",
    "720p", "1080p", "1080i", "480p", "2160p", "4k", "hd", "sd",
    "10bit", "8bit", "hdr", "remux", "proper", "repack", "internal",
    "amzn", "nf", "hulu", "dsnp", "atvp", "eng", "english",
]
_JUNK_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _JUNK_TOKENS) + r")\b",
                      re.IGNORECASE)


# A two/three-letter language code that some subtitle tools append just before
# the extension, e.g. "The Clown.en.srt" or "Matlock.S01E04.eng.srt". Stripped
# before any title text is recovered so the code never leaks into the parsed
# episode title (the "The Clown en" bug).
_LANG_CODE_RE = re.compile(r"\.[a-z]{2,3}\.(srt|vtt)$", re.IGNORECASE)


def strip_language_code(filename: str) -> str:
    """Remove a trailing language code that sits just before a subtitle extension.

    ``"The Clown.en.srt"`` -> ``"The Clown.srt"`` and
    ``"Matlock.S01E04.eng.srt"`` -> ``"Matlock.S01E04.srt"``. The subtitle
    extension is preserved so downstream format detection still works. Non
    subtitle names (and names without a language code) are returned unchanged.
    """
    return _LANG_CODE_RE.sub(r".\1", filename)


def clean_subtitle_filename(filename: str) -> str:
    """Return the base name with release/rip junk stripped.

    Removes the extension, bracketed groups ([...], {...}), common release-group
    and codec/quality tokens (dvdrip, x264, web-dl, 1080p, ...), then collapses
    the leftover separators to single spaces. The result is a human-readable
    string like "Matlock (1986) - S01E04 - The Stripper" suitable for both
    episode-number parsing and episode-title recovery.
    """
    # strip a trailing language code (".en.srt") before anything else so it can
    # never survive into the recovered episode title.
    filename = strip_language_code(filename)
    base = os.path.splitext(os.path.basename(filename))[0]
    # drop bracketed / braced groups outright (release tags, checksums, etc.)
    base = re.sub(r"[\[\{].*?[\]\}]", " ", base)
    # a trailing "-GROUP" release tag (e.g. "...-FLEET")
    base = re.sub(r"-[A-Za-z0-9]{2,}$", " ", base)
    # remove the junk tokens
    base = _JUNK_RE.sub(" ", base)
    # normalise dots/underscores that release names use as separators
    base = re.sub(r"[._]+", " ", base)
    base = re.sub(r"\s{2,}", " ", base).strip(" -_.")
    return base


def parse_episode_info(filename: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Extract (season, episode, episode_title) from a subtitle/video filename.

    Understands ``S01E03``, ``1x03``, ``Season 1 Episode 3``, multi-episode
    ``S01E01E02`` (keyed on the first episode) and, as a last resort, a compact
    ``103`` form. The episode title is whatever readable text follows the
    season/episode marker (with junk stripped), e.g.
    ``Matlock (1986) - S01E04 - The Stripper`` -> (1, 4, "The Stripper").
    Returns ``(None, None, None)`` when nothing episode-like is found.
    """
    cleaned = clean_subtitle_filename(filename)
    season = episode = None
    match_end = None
    for pat in _SE_PATTERNS:
        m = pat.search(cleaned)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
            match_end = m.end()
            break

    if season is None:
        # last resort: a bare "103" (S1E03) not adjacent to a 4-digit year
        m = _COMPACT_SE_RE.search(cleaned)
        if m and not _YEAR_RE.search(m.group(0)):
            season, episode = int(m.group(1)), int(m.group(2))
            match_end = m.end()

    episode_title = None
    if match_end is not None:
        tail = cleaned[match_end:]
        # remove a leading year in parentheses and separator punctuation
        tail = re.sub(r"^\s*[-_.]+\s*", " ", tail)
        tail = _YEAR_RE.sub(" ", tail)
        tail = re.sub(r"[-_.]+", " ", tail)
        tail = re.sub(r"\s{2,}", " ", tail).strip(" -_.")
        if tail:
            episode_title = tail
    return season, episode, episode_title


def parse_filename_metadata(filename: str, default_title: Optional[str] = None,
                            default_year: Optional[int] = None) -> MediaInfo:
    base = os.path.splitext(os.path.basename(filename))[0]
    season, episode, episode_title = parse_episode_info(filename)

    year = default_year
    ym = _YEAR_RE.search(base)
    if ym and not year:
        year = int(ym.group(0))

    title = default_title
    if not title:
        # take text before the SxxExx / year marker as a rough title
        cut = base
        for pat in _SE_PATTERNS + [_YEAR_RE]:
            m = pat.search(cut)
            if m:
                cut = cut[:m.start()]
                break
        title = re.sub(r"[._-]+", " ", cut).strip() or base

    media_type = "tv" if (season is not None or episode is not None) else "movie"
    # For a TV episode the show title defaults to the parsed/overridden title;
    # callers (e.g. create_fingerprint --show-title) may override it explicitly.
    show_title = title if media_type == "tv" else None
    return MediaInfo(title=title, year=year, media_type=media_type,
                     season=season, episode=episode, source=filename,
                     show_title=show_title, episode_title=episode_title)


# ---------------------------------------------------------------------------
# Subtitle parsing
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def parse_srt(path: str) -> List[Cue]:
    cues: List[Cue] = []
    try:
        subs = pysrt.open(path, encoding="utf-8")
    except Exception:
        subs = pysrt.from_string(_read_text(path))
    for item in subs:
        start = item.start.ordinal  # milliseconds
        end = item.end.ordinal
        text = item.text.replace("\n", " ")
        if text.strip():
            cues.append((start, end, text))
    return cues


def parse_vtt(path: str) -> List[Cue]:
    cues: List[Cue] = []
    for caption in webvtt.read(path):
        start = _ts_to_ms(caption.start)
        end = _ts_to_ms(caption.end)
        text = caption.text.replace("\n", " ")
        if text.strip():
            cues.append((start, end, text))
    return cues


def _ts_to_ms(ts: str) -> int:
    # "00:01:23.456" or "00:01:23,456"
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = "0", parts[0], parts[1]
    else:
        return 0
    sec, _, ms = s.partition(".")
    return (int(h) * 3600 + int(m) * 60 + int(sec)) * 1000 + int((ms or "0").ljust(3, "0")[:3])


def parse_subtitle_file(path: str) -> List[Cue]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".srt":
        return parse_srt(path)
    if ext == ".vtt":
        return parse_vtt(path)
    raise ValueError(f"Unsupported subtitle format: {ext}")


def find_subtitle_files(directory: str) -> List[str]:
    out = []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith((".srt", ".vtt")):
                out.append(os.path.join(root, f))
    return sorted(out)


# ---------------------------------------------------------------------------
# OpenSubtitles.org download (public REST search endpoint, no API key needed)
# ---------------------------------------------------------------------------

def _parse_query(query: str) -> Tuple[str, Optional[int]]:
    """'Matlock 1986' -> ('Matlock', 1986)."""
    m = _YEAR_RE.search(query)
    year = None
    title = query
    if m:
        year = int(m.group(0))
        title = (query[:m.start()] + query[m.end():]).strip()
    return title.strip(), year


def download_opensubtitles(query: str, out_dir: str, cfg: dict,
                           limit: int = 5, languages: str = "en",
                           media_type: Optional[str] = None) -> List[str]:
    """Search OpenSubtitles and download up to `limit` subtitle files.

    Two providers are supported (selected by config opensubtitles.provider):
      * "api"    -> official https://api.opensubtitles.com/api/v1 (needs api_key)
      * "legacy" -> public rest.opensubtitles.org/search (no key; often blocked)
      * "auto"   -> use the official API if an api_key is configured, else legacy

    `media_type` is an optional hint ("tv" / "movie"). For the official API this
    maps to type=episode / type=movie. When omitted, the API search tries
    episodes first and falls back to movies.

    Returns the list of extracted local subtitle file paths.
    """
    os_cfg = cfg.get("opensubtitles", {})
    provider = os_cfg.get("provider", "auto").lower()
    api_key = os_cfg.get("api_key", "")
    os.makedirs(out_dir, exist_ok=True)

    if provider == "auto":
        provider = "api" if api_key else "legacy"

    if provider == "api":
        if not api_key:
            logging.error("opensubtitles.provider='api' but no api_key configured. "
                          "Get a free key at https://www.opensubtitles.com/consumers")
            return []
        return _download_via_api(query, out_dir, cfg, limit, languages, media_type)
    return _download_via_legacy(query, out_dir, cfg, limit,
                                "eng" if languages in ("en", "eng") else languages)


def _media_type_to_search_type(media_type: Optional[str]) -> Optional[str]:
    if not media_type:
        return None
    return "movie" if media_type == "movie" else "episode"


def _api_search(api_url: str, headers: dict, title: str, year: Optional[int],
                lang: str, search_type: Optional[str], page: int = 1) -> list:
    """Perform a single OpenSubtitles API /subtitles search request.

    The query is lower-cased because the API issues a 301 redirect (which can
    drop query parameters on some clients) for non-lower-case queries.
    """
    params = {"query": title.lower(), "languages": lang, "page": page}
    if year:
        params["year"] = year
    if search_type:
        params["type"] = search_type
    resp = requests.get(f"{api_url}/subtitles", headers=headers,
                        params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _download_via_api(query: str, out_dir: str, cfg: dict,
                      limit: int, languages: str,
                      media_type: Optional[str] = None) -> List[str]:
    """Official opensubtitles.com REST API (requires a free API key)."""
    os_cfg = cfg.get("opensubtitles", {})
    api_url = os_cfg.get("api_url", "https://api.opensubtitles.com/api/v1").rstrip("/")
    api_key = os_cfg.get("api_key", "")
    user_agent = os_cfg.get("user_agent", "PhoneticFingerprint v1.0")
    lang = "en" if languages in ("en", "eng") else languages

    title, year = _parse_query(query)
    # The API requires a real, identifying User-Agent (app name + version).
    headers = {"Api-Key": api_key, "User-Agent": user_agent,
               "Content-Type": "application/json", "Accept": "application/json"}

    # Optional login to get a token for higher download quota
    token = None
    user, pwd = os_cfg.get("username", ""), os_cfg.get("password", "")
    if user and pwd:
        try:
            r = requests.post(f"{api_url}/login", headers=headers,
                              json={"username": user, "password": pwd}, timeout=30)
            if r.ok:
                token = r.json().get("token")
                logging.info("Logged in to OpenSubtitles as '%s'", user)
        except Exception as exc:
            logging.warning("OpenSubtitles login failed (continuing anonymously): %s", exc)

    # Decide which content type(s) to search. Explicit hint wins; otherwise try
    # episodes first (TV) then fall back to movies.
    hinted = _media_type_to_search_type(media_type)
    search_types = [hinted] if hinted else ["episode", "movie"]

    data = []
    used_type = None
    for st in search_types:
        logging.info("Searching OpenSubtitles API: query='%s' type=%s year=%s lang=%s",
                     title.lower(), st, year or "-", lang)
        try:
            data = _api_search(api_url, headers, title, year, lang, st)
        except Exception as exc:
            logging.error("OpenSubtitles API search failed: %s", exc)
            data = []
        if data:
            used_type = st
            break

    if not data:
        logging.warning("No subtitles found for '%s' (type tried: %s)",
                        query, ", ".join(str(s) for s in search_types))
        return []

    logging.info("Found %d subtitle result(s) (type=%s)", len(data), used_type)

    dl_headers = dict(headers)
    if token:
        dl_headers["Authorization"] = f"Bearer {token}"

    downloaded: List[str] = []
    for item in data:
        if len(downloaded) >= limit:
            break
        attrs = item.get("attributes", {})
        files = attrs.get("files", [])
        if not files:
            continue
        file_id = files[0].get("file_id")
        if not file_id:
            continue

        base_name = _build_subtitle_basename(attrs, files[0])
        try:
            r = requests.post(f"{api_url}/download", headers=dl_headers,
                              json={"file_id": file_id}, timeout=30)
            r.raise_for_status()
            link = r.json().get("link")
            if not link:
                logging.warning("No download link returned for file_id %s "
                                "(quota exhausted?)", file_id)
                continue
            paths = _download_and_extract(link, out_dir, {"User-Agent": user_agent},
                                          base_name=base_name)
            downloaded.extend(paths)
            time.sleep(1)
        except Exception as exc:
            logging.warning("Failed to download file_id %s: %s", file_id, exc)
    logging.info("Downloaded %d subtitle file(s) to %s", len(downloaded), out_dir)
    return downloaded


def _build_subtitle_basename(attrs: dict, file_entry: dict) -> str:
    """Build a clean, metadata-rich filename so downstream filename parsing
    recovers the correct show/season/episode/year.

    Uses the API's feature_details when available, otherwise the original
    uploaded file name.
    """
    fd = attrs.get("feature_details", {}) or {}
    parent = fd.get("parent_title") or fd.get("title") or "subtitle"
    year = fd.get("year")
    season = fd.get("season_number")
    episode = fd.get("episode_number")

    if season is not None and episode is not None:
        yr = f" ({year})" if year else ""
        name = f"{parent}{yr} S{int(season):02d}E{int(episode):02d}"
    else:
        # movie or missing S/E info -> use original file name if present
        orig = (file_entry.get("file_name") or fd.get("movie_name")
                or parent)
        name = orig
        if year and str(year) not in name:
            name = f"{name} ({year})"

    # sanitise for the filesystem
    name = re.sub(r"[^\w().\- ]+", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name or "subtitle"


def _download_via_legacy(query: str, out_dir: str, cfg: dict,
                         limit: int, languages: str) -> List[str]:
    """Legacy public rest.opensubtitles.org endpoint (no API key)."""
    os_cfg = cfg.get("opensubtitles", {})
    rest_url = os_cfg.get("rest_api_url", "https://rest.opensubtitles.org/search")
    user_agent = os_cfg.get("user_agent", "TemporaryUserAgent")

    title, year = _parse_query(query)
    headers = {"User-Agent": user_agent, "X-User-Agent": user_agent}
    q = title.replace(" ", "%20")
    url = f"{rest_url}/query-{q}/sublanguageid-{languages}"
    logging.info("Searching OpenSubtitles (legacy): %s", url)

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        items = resp.json()
    except Exception as exc:
        logging.error("OpenSubtitles (legacy) search failed: %s", exc)
        logging.error("The legacy endpoint is frequently blocked/rate-limited. "
                      "Configure an official API key (opensubtitles.api_key) or "
                      "download .srt files manually and use --dir.")
        return []

    if not isinstance(items, list) or not items:
        logging.warning("No subtitles found for '%s'", query)
        return []

    if year:
        filtered = [it for it in items if str(it.get("MovieYear", "")) == str(year)]
        if filtered:
            items = filtered

    downloaded: List[str] = []
    for it in items:
        if len(downloaded) >= limit:
            break
        dl = it.get("SubDownloadLink") or it.get("ZipDownloadLink")
        if not dl:
            continue
        try:
            paths = _download_and_extract(dl, out_dir, headers)
            downloaded.extend(paths)
            time.sleep(1)
        except Exception as exc:
            logging.warning("Failed to download %s: %s", dl, exc)
    logging.info("Downloaded %d subtitle file(s) to %s", len(downloaded), out_dir)
    return downloaded


def _unique_path(out_dir: str, base_name: str, ext: str) -> str:
    """Return a non-colliding path out_dir/base_name(ext), adding a suffix if needed."""
    base_name = base_name or "subtitle"
    candidate = os.path.join(out_dir, f"{base_name}{ext}")
    i = 2
    while os.path.exists(candidate):
        candidate = os.path.join(out_dir, f"{base_name} ({i}){ext}")
        i += 1
    return candidate


def _download_and_extract(url: str, out_dir: str, headers: dict,
                          base_name: Optional[str] = None) -> List[str]:
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    content = resp.content
    out: List[str] = []

    # pick extension based on the link (official API links usually end in .srt)
    lower_url = url.lower()
    ext = ".vtt" if ".vtt" in lower_url else ".srt"
    fallback_base = base_name or f"sub_{abs(hash(url)) % 10**8}"

    # gzip (.gz) - most legacy SubDownloadLink results
    if lower_url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(content)
            fname = _unique_path(out_dir, fallback_base, ext)
            with open(fname, "wb") as fh:
                fh.write(data)
            out.append(fname)
            return out
        except OSError:
            pass

    # zip archive
    if content[:2] == b"PK":
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        with zipfile.ZipFile(tmp_path) as zf:
            sub_names = [n for n in zf.namelist()
                         if n.lower().endswith((".srt", ".vtt"))]
            for idx, name in enumerate(sub_names):
                sub_ext = ".vtt" if name.lower().endswith(".vtt") else ".srt"
                bn = fallback_base if len(sub_names) == 1 else f"{fallback_base} {idx + 1}"
                target = _unique_path(out_dir, bn, sub_ext)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                out.append(target)
        os.unlink(tmp_path)
        return out

    # raw subtitle (official API download link)
    fname = _unique_path(out_dir, fallback_base, ext)
    with open(fname, "wb") as fh:
        fh.write(content)
    out.append(fname)
    return out
