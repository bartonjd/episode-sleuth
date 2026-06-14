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
    re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})"),          # S01E02
    re.compile(r"(\d{1,2})x(\d{2,3})"),                          # 1x02
    re.compile(r"[Ss]eason[ ._-]?(\d{1,2}).*?[Ee]pisode[ ._-]?(\d{1,3})", re.I),
]
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def parse_filename_metadata(filename: str, default_title: Optional[str] = None,
                            default_year: Optional[int] = None) -> MediaInfo:
    base = os.path.splitext(os.path.basename(filename))[0]
    season = episode = None
    for pat in _SE_PATTERNS:
        m = pat.search(base)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
            break

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
    return MediaInfo(title=title, year=year, media_type=media_type,
                     season=season, episode=episode, source=filename)


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
                           limit: int = 5, languages: str = "en") -> List[str]:
    """Search OpenSubtitles and download up to `limit` subtitle files.

    Two providers are supported (selected by config opensubtitles.provider):
      * "api"    -> official https://api.opensubtitles.com/api/v1 (needs api_key)
      * "legacy" -> public rest.opensubtitles.org/search (no key; often blocked)
      * "auto"   -> use the official API if an api_key is configured, else legacy

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
        return _download_via_api(query, out_dir, cfg, limit, languages)
    return _download_via_legacy(query, out_dir, cfg, limit,
                                "eng" if languages in ("en", "eng") else languages)


def _download_via_api(query: str, out_dir: str, cfg: dict,
                      limit: int, languages: str) -> List[str]:
    """Official opensubtitles.com REST API (requires a free API key)."""
    os_cfg = cfg.get("opensubtitles", {})
    api_url = os_cfg.get("api_url", "https://api.opensubtitles.com/api/v1").rstrip("/")
    api_key = os_cfg.get("api_key", "")
    user_agent = os_cfg.get("user_agent", "PhoneticFingerprint v1.0")
    lang = "en" if languages in ("en", "eng") else languages

    title, year = _parse_query(query)
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
        except Exception as exc:
            logging.warning("OpenSubtitles login failed (continuing anonymously): %s", exc)

    params = {"query": title, "languages": lang}
    if year:
        params["year"] = year
    logging.info("Searching OpenSubtitles API for '%s' (lang=%s)", title, lang)
    try:
        resp = requests.get(f"{api_url}/subtitles", headers=headers,
                            params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as exc:
        logging.error("OpenSubtitles API search failed: %s", exc)
        return []

    if not data:
        logging.warning("No subtitles found for '%s'", query)
        return []

    dl_headers = dict(headers)
    if token:
        dl_headers["Authorization"] = f"Bearer {token}"

    downloaded: List[str] = []
    for item in data:
        if len(downloaded) >= limit:
            break
        files = item.get("attributes", {}).get("files", [])
        if not files:
            continue
        file_id = files[0].get("file_id")
        if not file_id:
            continue
        try:
            r = requests.post(f"{api_url}/download", headers=dl_headers,
                              json={"file_id": file_id}, timeout=30)
            r.raise_for_status()
            link = r.json().get("link")
            if not link:
                continue
            paths = _download_and_extract(link, out_dir, {"User-Agent": user_agent})
            downloaded.extend(paths)
            time.sleep(1)
        except Exception as exc:
            logging.warning("Failed to download file_id %s: %s", file_id, exc)
    logging.info("Downloaded %d subtitle file(s) to %s", len(downloaded), out_dir)
    return downloaded


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


def _download_and_extract(url: str, out_dir: str, headers: dict) -> List[str]:
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    content = resp.content
    out: List[str] = []

    # gzip (.gz) — most SubDownloadLink results
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(content)
            fname = os.path.join(out_dir, f"sub_{abs(hash(url)) % 10**8}.srt")
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
            for name in zf.namelist():
                if name.lower().endswith((".srt", ".vtt")):
                    target = os.path.join(out_dir, os.path.basename(name))
                    with zf.open(name) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    out.append(target)
        os.unlink(tmp_path)
        return out

    # raw subtitle
    fname = os.path.join(out_dir, f"sub_{abs(hash(url)) % 10**8}.srt")
    with open(fname, "wb") as fh:
        fh.write(content)
    out.append(fname)
    return out
