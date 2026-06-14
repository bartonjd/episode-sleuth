"""
fingerprint_core.py
===================
Shared utilities for the phonetic audio-fingerprinting system.

The pipeline (used identically by subtitles, audio files and live audio) is:

    raw text  ->  clean/normalize  ->  Double Metaphone phonetic encoding
              ->  N-word phonetic shingles (sliding window)
              ->  stable hash of each shingle

This module also contains the SQLite-backed fingerprint database and the
matching / scoring logic so that every entry-point script behaves consistently.
"""

import os
import re
import json
import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Iterable, Optional, Tuple

from metaphone import doublemetaphone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config(path: Optional[str] = None) -> dict:
    """Load the JSON configuration file, falling back to sensible defaults."""
    path = path or DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logging.warning("Could not load config from %s (%s); using defaults.", path, exc)
        return {
            "fingerprint": {
                "shingle_sizes": [3, 4, 5],
                "min_word_length": 2,
                "metaphone_primary_only": False,
                "drop_stopwords": False,
                "hash_algorithm": "md5",
                "hash_length": 16,
            },
            "database": {"path": "fingerprints.db"},
            "matching": {
                "confidence_threshold": 0.15,
                "time_window_seconds": 30,
                "min_matches": 3,
                "fuzzy_edit_distance": 1,
                "enable_fuzzy": True,
                "top_n_results": 5,
            },
            "audio": {"sample_rate": 16000, "chunk_seconds": 8, "overlap_seconds": 2,
                      "energy_threshold": 300},
            "stt": {"engine": "vosk",
                    "vosk_model_path": "models/vosk-model-small-en-us-0.15",
                    "google_language": "en-US"},
            "opensubtitles": {
                "base_url": "https://www.opensubtitles.org",
                "rest_api_url": "https://rest.opensubtitles.org/search",
                "user_agent": "TemporaryUserAgent",
                "download_dir": "downloads",
            },
            "logging": {"level": "INFO"},
        }


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Text normalization + phonetic encoding
# ---------------------------------------------------------------------------

# A small, common stop-word list. Disabled by default in config because dropping
# words changes shingle alignment between the subtitle and the spoken audio.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "to",
    "of", "in", "on", "at", "for", "with", "as", "by", "it", "this", "that",
}

# Strip subtitle markup such as <i>, {\an8}, [music], (laughs), speaker labels.
_TAG_RE = re.compile(r"<[^>]+>")
_CURLY_RE = re.compile(r"\{[^}]*\}")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_PAREN_RE = re.compile(r"\([^)]*\)")
_SPEAKER_RE = re.compile(r"^[A-Z][A-Z0-9 .'-]{1,20}:")
_NONALPHA_RE = re.compile(r"[^a-z0-9'\s]")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Normalize a raw subtitle / transcript line into lowercase words."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = _CURLY_RE.sub(" ", text)
    text = _BRACKET_RE.sub(" ", text)   # remove [sound] cues
    text = _PAREN_RE.sub(" ", text)     # remove (whisper) cues
    text = _SPEAKER_RE.sub(" ", text)   # remove leading "JOHN:" speaker labels
    text = text.replace("-", " ")
    text = text.lower()
    text = _NONALPHA_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def tokenize(text: str, min_word_length: int = 2, drop_stopwords: bool = False) -> List[str]:
    words = clean_text(text).split()
    out = []
    for w in words:
        if len(w) < min_word_length and not w.isdigit():
            continue
        if drop_stopwords and w in _STOPWORDS:
            continue
        out.append(w)
    return out


def phonetic_encode_word(word: str, primary_only: bool = False) -> str:
    """Return a Double Metaphone code for a single word.

    Falls back to the raw word when metaphone yields nothing (e.g. digits)."""
    primary, secondary = doublemetaphone(word)
    if not primary and not secondary:
        return word  # numbers / unencodable tokens
    if primary_only or not secondary:
        return primary or word
    # Use both codes joined so two words that share either code stay comparable
    # via the primary; we key shingles on the primary code for stability.
    return primary or secondary


def phonetic_tokens(words: Iterable[str], primary_only: bool = False) -> List[str]:
    return [phonetic_encode_word(w, primary_only) for w in words]


# ---------------------------------------------------------------------------
# Shingling + hashing
# ---------------------------------------------------------------------------

def make_shingles(tokens: List[str], size: int) -> List[str]:
    """Sliding-window shingles of `size` consecutive phonetic tokens."""
    if size <= 0 or len(tokens) < size:
        return []
    return [" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1)]


def hash_shingle(shingle: str, algorithm: str = "md5", length: int = 16) -> str:
    h = hashlib.new(algorithm)
    h.update(shingle.encode("utf-8"))
    return h.hexdigest()[:length]


@dataclass
class FingerprintConfig:
    shingle_sizes: List[int] = field(default_factory=lambda: [3, 4, 5])
    min_word_length: int = 2
    metaphone_primary_only: bool = False
    drop_stopwords: bool = False
    hash_algorithm: str = "md5"
    hash_length: int = 16

    @classmethod
    def from_config(cls, cfg: dict) -> "FingerprintConfig":
        fp = cfg.get("fingerprint", {})
        return cls(
            shingle_sizes=fp.get("shingle_sizes", [3, 4, 5]),
            min_word_length=fp.get("min_word_length", 2),
            metaphone_primary_only=fp.get("metaphone_primary_only", False),
            drop_stopwords=fp.get("drop_stopwords", False),
            hash_algorithm=fp.get("hash_algorithm", "md5"),
            hash_length=fp.get("hash_length", 16),
        )


def fingerprint_text(text: str, cfg: FingerprintConfig) -> List[Tuple[str, int]]:
    """Convert a block of text into a list of (hash, shingle_size) tuples.

    The same function is used for subtitles and for STT output, guaranteeing
    that the encoding is identical on both sides of a match.
    """
    tokens = tokenize(text, cfg.min_word_length, cfg.drop_stopwords)
    ph = phonetic_tokens(tokens, cfg.metaphone_primary_only)
    results: List[Tuple[str, int]] = []
    for size in cfg.shingle_sizes:
        for sh in make_shingles(ph, size):
            results.append((hash_shingle(sh, cfg.hash_algorithm, cfg.hash_length), size))
    return results


# ---------------------------------------------------------------------------
# Fingerprint database (SQLite)
# ---------------------------------------------------------------------------

@dataclass
class MediaInfo:
    title: str
    year: Optional[int] = None
    media_type: str = "tv"          # "tv" or "movie"
    season: Optional[int] = None
    episode: Optional[int] = None
    source: str = ""                # original file / url

    def label(self) -> str:
        y = f" ({self.year})" if self.year else ""
        if self.media_type == "movie":
            return f"{self.title}{y}"
        se = ""
        if self.season is not None and self.episode is not None:
            se = f" S{self.season:02d}E{self.episode:02d}"
        elif self.episode is not None:
            se = f" E{self.episode:02d}"
        return f"{self.title}{y}{se}"


class FingerprintDB:
    """SQLite store mapping shingle hashes -> media + timestamp."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                year INTEGER,
                media_type TEXT,
                season INTEGER,
                episode INTEGER,
                source TEXT,
                UNIQUE(title, year, media_type, season, episode, source)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fingerprints (
                hash TEXT NOT NULL,
                shingle_size INTEGER,
                media_id INTEGER NOT NULL,
                start_ms INTEGER,
                end_ms INTEGER,
                FOREIGN KEY(media_id) REFERENCES media(id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_hash ON fingerprints(hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_media ON fingerprints(media_id)")

        # --- Acoustic (Chromaprint) fingerprint tables -----------------------
        # One row per audio SEGMENT (e.g. 30 s). The raw Chromaprint fingerprint
        # (list of uint32 frames) is stored as a BLOB so it can be re-aligned
        # against a query for an exact bit-error-rate score.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS acoustic_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                segment_index INTEGER,
                start_ms INTEGER,
                end_ms INTEGER,
                num_frames INTEGER,
                fingerprint BLOB,
                FOREIGN KEY(media_id) REFERENCES media(id)
            )
            """
        )
        # Inverted index of individual sub-fingerprints (one Chromaprint frame
        # integer) -> segment + position, for fast candidate lookup / offset
        # histogram alignment.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS acoustic_index (
                subfp INTEGER NOT NULL,
                segment_id INTEGER NOT NULL,
                media_id INTEGER NOT NULL,
                position INTEGER,
                FOREIGN KEY(segment_id) REFERENCES acoustic_segments(id),
                FOREIGN KEY(media_id) REFERENCES media(id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_subfp ON acoustic_index(subfp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_seg ON acoustic_index(segment_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ac_seg_media ON acoustic_segments(media_id)")
        self.conn.commit()

    def get_or_create_media(self, info: MediaInfo) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """SELECT id FROM media WHERE title=? AND IFNULL(year,-1)=IFNULL(?,-1)
               AND media_type=? AND IFNULL(season,-1)=IFNULL(?,-1)
               AND IFNULL(episode,-1)=IFNULL(?,-1) AND source=?""",
            (info.title, info.year, info.media_type, info.season, info.episode, info.source),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            """INSERT INTO media (title, year, media_type, season, episode, source)
               VALUES (?,?,?,?,?,?)""",
            (info.title, info.year, info.media_type, info.season, info.episode, info.source),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_fingerprints(self, media_id: int,
                         rows: Iterable[Tuple[str, int, Optional[int], Optional[int]]]) -> int:
        """rows = iterable of (hash, shingle_size, start_ms, end_ms)."""
        cur = self.conn.cursor()
        data = [(h, s, media_id, a, b) for (h, s, a, b) in rows]
        cur.executemany(
            "INSERT INTO fingerprints (hash, shingle_size, media_id, start_ms, end_ms) "
            "VALUES (?,?,?,?,?)",
            data,
        )
        self.conn.commit()
        return len(data)

    def clear_media(self, info: MediaInfo) -> None:
        """Remove an existing media entry and its fingerprints (re-index)."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT id FROM media WHERE title=? AND IFNULL(year,-1)=IFNULL(?,-1)
               AND media_type=? AND IFNULL(season,-1)=IFNULL(?,-1)
               AND IFNULL(episode,-1)=IFNULL(?,-1) AND source=?""",
            (info.title, info.year, info.media_type, info.season, info.episode, info.source),
        )
        for row in cur.fetchall():
            cur.execute("DELETE FROM fingerprints WHERE media_id=?", (row["id"],))
            cur.execute("DELETE FROM acoustic_index WHERE media_id=?", (row["id"],))
            cur.execute("DELETE FROM acoustic_segments WHERE media_id=?", (row["id"],))
            cur.execute("DELETE FROM media WHERE id=?", (row["id"],))
        self.conn.commit()

    def clear_media_acoustic(self, info: MediaInfo) -> None:
        """Remove only the acoustic fingerprints for a media entry (keeps
        phonetic fingerprints and the media row intact)."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT id FROM media WHERE title=? AND IFNULL(year,-1)=IFNULL(?,-1)
               AND media_type=? AND IFNULL(season,-1)=IFNULL(?,-1)
               AND IFNULL(episode,-1)=IFNULL(?,-1) AND source=?""",
            (info.title, info.year, info.media_type, info.season, info.episode, info.source),
        )
        for row in cur.fetchall():
            cur.execute("DELETE FROM acoustic_index WHERE media_id=?", (row["id"],))
            cur.execute("DELETE FROM acoustic_segments WHERE media_id=?", (row["id"],))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Smart-skip helpers: has this file already been fingerprinted?
    # ------------------------------------------------------------------

    def file_has_phonetic(self, source: str) -> bool:
        """Return True if a media row with this `source` (file path) already has
        at least one phonetic fingerprint row."""
        if not source:
            return False
        cur = self.conn.cursor()
        cur.execute(
            """SELECT 1 FROM fingerprints f
               JOIN media m ON m.id = f.media_id
               WHERE m.source = ? LIMIT 1""",
            (source,),
        )
        return cur.fetchone() is not None

    def file_has_acoustic(self, source: str) -> bool:
        """Return True if a media row with this `source` (file path) already has
        at least one acoustic segment row."""
        if not source:
            return False
        cur = self.conn.cursor()
        cur.execute(
            """SELECT 1 FROM acoustic_segments s
               JOIN media m ON m.id = s.media_id
               WHERE m.source = ? LIMIT 1""",
            (source,),
        )
        return cur.fetchone() is not None

    def file_already_fingerprinted(self, source: str, acoustic: bool = False) -> bool:
        """Convenience wrapper: check whether `source` already has fingerprints.

        When ``acoustic`` is False (default) the phonetic ``fingerprints`` table
        is checked; when True the ``acoustic_segments`` table is checked.
        """
        if acoustic:
            return self.file_has_acoustic(source)
        return self.file_has_phonetic(source)

    # ------------------------------------------------------------------
    # Acoustic (Chromaprint) storage & lookup
    # ------------------------------------------------------------------

    def add_acoustic_segment(self, media_id: int, segment_index: int,
                             start_ms: int, end_ms: int,
                             raw_ints: List[int], index_stride: int = 1) -> int:
        """Store one segment's Chromaprint fingerprint plus its inverted-index
        rows. Returns the new segment_id.

        `raw_ints` is the list of uint32 Chromaprint frames for the segment.
        `index_stride` lets you index every Nth frame to shrink the index.
        """
        import struct
        blob = struct.pack(f"<{len(raw_ints)}I",
                           *[(x & 0xFFFFFFFF) for x in raw_ints])
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO acoustic_segments
               (media_id, segment_index, start_ms, end_ms, num_frames, fingerprint)
               VALUES (?,?,?,?,?,?)""",
            (media_id, segment_index, start_ms, end_ms, len(raw_ints),
             sqlite3.Binary(blob)),
        )
        segment_id = cur.lastrowid
        idx_rows = [
            ((raw_ints[pos] & 0xFFFFFFFF), segment_id, media_id, pos)
            for pos in range(0, len(raw_ints), max(1, index_stride))
        ]
        if idx_rows:
            cur.executemany(
                "INSERT INTO acoustic_index (subfp, segment_id, media_id, position) "
                "VALUES (?,?,?,?)",
                idx_rows,
            )
        self.conn.commit()
        return segment_id

    def lookup_acoustic_index(self, subfps: Iterable[int]) -> List[sqlite3.Row]:
        """Return inverted-index rows (subfp, segment_id, media_id, position)
        matching the given sub-fingerprint integers."""
        subfps = list({(x & 0xFFFFFFFF) for x in subfps})
        if not subfps:
            return []
        results: List[sqlite3.Row] = []
        cur = self.conn.cursor()
        CHUNK = 400
        for i in range(0, len(subfps), CHUNK):
            chunk = subfps[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""SELECT subfp, segment_id, media_id, position
                    FROM acoustic_index WHERE subfp IN ({placeholders})""",
                chunk,
            )
            results.extend(cur.fetchall())
        return results

    def get_acoustic_segment(self, segment_id: int) -> Optional[dict]:
        """Return a segment's metadata + decoded raw fingerprint list."""
        import struct
        cur = self.conn.cursor()
        cur.execute(
            """SELECT s.*, m.title, m.year, m.media_type, m.season, m.episode
               FROM acoustic_segments s JOIN media m ON s.media_id = m.id
               WHERE s.id=?""",
            (segment_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        blob = row["fingerprint"]
        n = row["num_frames"] or (len(blob) // 4)
        raw = list(struct.unpack(f"<{n}I", blob)) if blob else []
        return {
            "id": row["id"], "media_id": row["media_id"],
            "segment_index": row["segment_index"],
            "start_ms": row["start_ms"], "end_ms": row["end_ms"],
            "num_frames": n, "raw": raw,
            "title": row["title"], "year": row["year"],
            "media_type": row["media_type"], "season": row["season"],
            "episode": row["episode"],
        }

    def all_acoustic_segment_ids(self) -> List[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM acoustic_segments")
        return [r["id"] for r in cur.fetchall()]

    def lookup(self, hashes: Iterable[str]) -> List[sqlite3.Row]:
        """Return all fingerprint rows (joined with media) matching given hashes."""
        hashes = list(set(hashes))
        if not hashes:
            return []
        results: List[sqlite3.Row] = []
        cur = self.conn.cursor()
        # chunk to stay within SQLite's variable limit
        CHUNK = 400
        for i in range(0, len(hashes), CHUNK):
            chunk = hashes[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"""SELECT f.hash, f.shingle_size, f.start_ms, f.end_ms,
                           m.id AS media_id, m.title, m.year, m.media_type,
                           m.season, m.episode
                    FROM fingerprints f JOIN media m ON f.media_id = m.id
                    WHERE f.hash IN ({placeholders})""",
                chunk,
            )
            results.extend(cur.fetchall())
        return results

    def all_hashes_for_media(self, media_id: int) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT hash FROM fingerprints WHERE media_id=?", (media_id,))
        return [r["hash"] for r in cur.fetchall()]

    def stats(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM media")
        media_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM fingerprints")
        fp_count = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM acoustic_segments")
        ac_seg = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM acoustic_index")
        ac_idx = cur.fetchone()["c"]
        return {"media": media_count, "fingerprints": fp_count,
                "acoustic_segments": ac_seg, "acoustic_subfps": ac_idx}

    def list_media(self) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM media ORDER BY title, season, episode")
        return cur.fetchall()

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Matching / scoring
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    media: MediaInfo
    media_id: int
    confidence: float
    match_count: int
    query_count: int
    window_start_ms: Optional[int] = None
    window_end_ms: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "title": self.media.title,
            "year": self.media.year,
            "media_type": self.media.media_type,
            "season": self.media.season,
            "episode": self.media.episode,
            "label": self.media.label(),
            "confidence": round(self.confidence, 4),
            "matches": self.match_count,
            "query_shingles": self.query_count,
        }


def score_matches(query_hashes: List[str],
                  db_rows: List[sqlite3.Row],
                  matching_cfg: dict) -> List[MatchResult]:
    """Aggregate db lookup rows into ranked MatchResults.

    Confidence = (# query shingles that matched a media) / (# query shingles),
    with a time-window bonus that rewards matches clustered in time.
    """
    if not query_hashes:
        return []

    query_set = set(query_hashes)
    top_n = matching_cfg.get("top_n_results", 5)
    window_ms = matching_cfg.get("time_window_seconds", 30) * 1000

    # group rows by media
    per_media: Dict[int, Dict] = {}
    for row in db_rows:
        mid = row["media_id"]
        bucket = per_media.setdefault(mid, {
            "info": MediaInfo(
                title=row["title"], year=row["year"], media_type=row["media_type"],
                season=row["season"], episode=row["episode"],
            ),
            "hashes": set(),
            "timestamps": [],
        })
        bucket["hashes"].add(row["hash"])
        if row["start_ms"] is not None:
            bucket["timestamps"].append(row["start_ms"])

    results: List[MatchResult] = []
    for mid, bucket in per_media.items():
        matched = bucket["hashes"] & query_set
        match_count = len(matched)
        base_conf = match_count / max(1, len(query_set))

        # time-window bonus: find densest cluster of matched timestamps
        win_start = win_end = None
        cluster_bonus = 0.0
        ts = sorted(bucket["timestamps"])
        if ts:
            best = 1
            lo = 0
            for hi in range(len(ts)):
                while ts[hi] - ts[lo] > window_ms:
                    lo += 1
                span = hi - lo + 1
                if span > best:
                    best = span
                    win_start, win_end = ts[lo], ts[hi]
            # normalize cluster density into a small bonus (max +0.25)
            cluster_bonus = min(0.25, (best / max(1, match_count)) * 0.25)

        confidence = min(1.0, base_conf + cluster_bonus)
        results.append(MatchResult(
            media=bucket["info"], media_id=mid, confidence=confidence,
            match_count=match_count, query_count=len(query_set),
            window_start_ms=win_start, window_end_ms=win_end,
        ))

    results.sort(key=lambda r: (r.confidence, r.match_count), reverse=True)
    return results[:top_n]
