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


def phonetic_token_stream(text: str, cfg: FingerprintConfig) -> List[str]:
    """Return the in-order list of phonetic tokens for a text block.

    This is the *unhashed* counterpart of :func:`fingerprint_text`: it uses the
    exact same tokenization + Double-Metaphone encoding, so a query stream and a
    reference stream are directly comparable token-for-token. Used by the fuzzy
    (order-preserving) matcher, which needs the raw token sequence rather than
    opaque shingle hashes.
    """
    tokens = tokenize(text, cfg.min_word_length, cfg.drop_stopwords)
    return phonetic_tokens(tokens, cfg.metaphone_primary_only)


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

        # --- Phonetic TOKEN STREAM (for fuzzy / order-preserving matching) ----
        # The `fingerprints` table stores only opaque shingle *hashes*, which
        # only ever match exactly. A single STT word error breaks every shingle
        # that contains that word, so exact-hash recall degrades fast on real
        # microphone audio. To recover, we also persist the raw ordered stream
        # of phonetic tokens per media (one row per media, tokens packed as a
        # space-joined string plus a parallel JSON list of start_ms). The fuzzy
        # matcher loads this stream for a small candidate shortlist and scores
        # the longest order-preserving common subsequence against the query,
        # which tolerates missing / mis-heard / inserted words.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS media_tokens (
                media_id INTEGER PRIMARY KEY,
                tokens TEXT NOT NULL,
                starts TEXT NOT NULL,
                FOREIGN KEY(media_id) REFERENCES media(id)
            )
            """
        )

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

    def add_token_stream(self, media_id: int, tokens: List[str],
                         starts: List[Optional[int]]) -> int:
        """Persist the ordered phonetic token stream for a media row.

        ``tokens`` is the full in-order list of Double-Metaphone codes for the
        whole subtitle / transcript, and ``starts`` the parallel list of cue
        start times (ms) so a fuzzy match can report a time window. Stored as a
        single row (tokens space-joined, starts JSON-encoded) so the fuzzy
        matcher can load an entire candidate's stream in one cheap read.
        """
        if not tokens:
            return 0
        cur = self.conn.cursor()
        cur.execute("DELETE FROM media_tokens WHERE media_id=?", (media_id,))
        cur.execute(
            "INSERT INTO media_tokens (media_id, tokens, starts) VALUES (?,?,?)",
            (media_id, " ".join(tokens), json.dumps(starts)),
        )
        self.conn.commit()
        return len(tokens)

    def get_token_stream(self, media_id: int
                         ) -> Tuple[List[str], List[Optional[int]]]:
        """Return (tokens, starts) for a media row, or ([], []) if absent."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT tokens, starts FROM media_tokens WHERE media_id=?", (media_id,))
        row = cur.fetchone()
        if not row or not row["tokens"]:
            return [], []
        tokens = row["tokens"].split(" ")
        try:
            starts = json.loads(row["starts"])
        except (ValueError, TypeError):
            starts = [None] * len(tokens)
        return tokens, starts

    def has_token_stream(self, media_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM media_tokens WHERE media_id=? LIMIT 1", (media_id,))
        return cur.fetchone() is not None

    def all_token_stream_media_ids(self) -> List[int]:
        """All media ids that have a stored phonetic token stream (used by the
        fuzzy matcher's full-database fallback when no acoustic shortlist)."""
        cur = self.conn.cursor()
        cur.execute("SELECT media_id FROM media_tokens")
        return [r["media_id"] for r in cur.fetchall()]

    def media_info(self, media_id: int) -> Optional["MediaInfo"]:
        """Return a MediaInfo for a media id, or None if it does not exist."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT title, year, media_type, season, episode, source "
            "FROM media WHERE id=?", (media_id,))
        r = cur.fetchone()
        if not r:
            return None
        return MediaInfo(
            title=r["title"], year=r["year"], media_type=r["media_type"],
            season=r["season"], episode=r["episode"], source=r["source"] or "",
        )

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
            cur.execute("DELETE FROM media_tokens WHERE media_id=?", (row["id"],))
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

    def lookup(self, hashes: Iterable[str],
               media_ids: Optional[Iterable[int]] = None) -> List[sqlite3.Row]:
        """Return all fingerprint rows (joined with media) matching given hashes.

        If ``media_ids`` is given, the search is *scoped* to only those media
        rows. This powers the two-stage hybrid workflow: acoustic matching first
        produces a shortlist of candidate episodes, and the (much larger)
        phonetic search is then restricted to just those candidates, which is
        dramatically faster than scanning the whole fingerprint table.
        """
        hashes = list(set(hashes))
        if not hashes:
            return []

        mid_list: Optional[List[int]] = None
        if media_ids is not None:
            mid_list = list({int(m) for m in media_ids})
            if not mid_list:
                # An explicit but empty candidate set means "nothing to search".
                return []
        mid_clause = ""
        if mid_list is not None:
            mid_ph = ",".join("?" * len(mid_list))
            mid_clause = f" AND f.media_id IN ({mid_ph})"

        results: List[sqlite3.Row] = []
        cur = self.conn.cursor()
        # chunk to stay within SQLite's variable limit
        CHUNK = 400
        for i in range(0, len(hashes), CHUNK):
            chunk = hashes[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            params = list(chunk)
            if mid_list is not None:
                params += mid_list
            cur.execute(
                f"""SELECT f.hash, f.shingle_size, f.start_ms, f.end_ms,
                           m.id AS media_id, m.title, m.year, m.media_type,
                           m.season, m.episode
                    FROM fingerprints f JOIN media m ON f.media_id = m.id
                    WHERE f.hash IN ({placeholders}){mid_clause}""",
                params,
            )
            results.extend(cur.fetchall())
        return results

    def all_hashes_for_media(self, media_id: int) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT hash FROM fingerprints WHERE media_id=?", (media_id,))
        return [r["hash"] for r in cur.fetchall()]

    def media_ids_for_episodes(
            self, episodes: Iterable[Tuple[Optional[str], Optional[int], Optional[int]]],
    ) -> List[int]:
        """Resolve (title, season, episode) keys to *all* matching media ids.

        The same episode is often stored under more than one ``media`` row — e.g.
        a subtitle row (which carries the phonetic fingerprints) and a separate
        media-file row (which carries the acoustic segments). The two-stage
        hybrid workflow shortlists candidates acoustically (media-file rows) but
        must then run phonetic matching against the subtitle rows for the *same*
        episode. This method bridges the two by matching on the episode identity
        (title + season + episode) rather than the raw media id, so scoping works
        regardless of which row type produced the candidate.
        """
        keys = list(episodes)
        if not keys:
            return []
        cur = self.conn.cursor()
        found: List[int] = []
        seen = set()
        for title, season, episode in keys:
            clauses: List[str] = []
            params: List = []
            # For each field, a NULL value must use the SQL `IS NULL` literal
            # (which takes NO bind parameter); a non-NULL value uses `= ?` with
            # exactly one bind parameter. Mixing `IS ?` with a NULL python value
            # is the source of the original "Incorrect number of bindings" crash:
            # `IS ?` keeps a placeholder but no parameter was appended for it.
            for col, val in (("title", title), ("season", season),
                             ("episode", episode)):
                if val is None:
                    clauses.append(f"{col} IS NULL")
                else:
                    clauses.append(f"{col} = ?")
                    params.append(val)
            cur.execute(
                f"SELECT id FROM media WHERE {' AND '.join(clauses)}", params)
            for r in cur.fetchall():
                if r["id"] not in seen:
                    seen.add(r["id"])
                    found.append(r["id"])
        return found

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



# ---------------------------------------------------------------------------
# Fuzzy (order-preserving) phonetic matching
# ---------------------------------------------------------------------------
#
# Why a second matcher?
# ---------------------
# `score_matches` above compares *shingle hashes* and only ever counts EXACT
# hits. A shingle of size N hashes N consecutive phonetic tokens together, so a
# single mis-heard / dropped / inserted word from the STT engine destroys every
# shingle that overlaps it (up to N per size, across all sizes). On clean
# subtitle-vs-subtitle data that is fine, but on a real microphone re-recording
# the STT transcript is noisy enough that exact-hash recall collapses.
#
# The fuzzy matcher works on the raw *token stream* instead. Given the query's
# phonetic tokens Q and a candidate reference stream R, it finds the longest
# common subsequence (LCS) of tokens that appears in the SAME ORDER in both,
# with arbitrarily large gaps allowed on either side. That means:
#   * a dropped query word  -> just shortens the subsequence slightly
#   * an inserted query word -> skipped over (it is a gap)
#   * a mis-heard word       -> contributes nothing, but its neighbours still do
# so the score degrades gracefully instead of falling off a cliff.
#
# LCS over two long sequences is O(n*m); we make it cheap with the classic
# "LCS via Longest Increasing Subsequence" trick: because we only need the
# length (not the alignment), we map every query token to the reference
# positions where it occurs, lay those positions out in query order (each query
# index's positions in DESCENDING order so one query token is used at most once
# per increasing run), and take the Longest Strictly Increasing Subsequence of
# the resulting position list. Its length == LCS length, in O(M log M) where M
# is the number of (query-token, reference-position) co-occurrences.


def _lis_length(seq: List[int]) -> int:
    """Length of the Longest Strictly Increasing Subsequence of ``seq``."""
    import bisect
    tails: List[int] = []
    for x in seq:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)


def _fuzzy_lcs_length(query: List[str], ref: List[str],
                      ref_index: Optional[Dict[str, List[int]]] = None
                      ) -> Tuple[int, Optional[int], Optional[int]]:
    """Return (lcs_len, first_ref_pos, last_ref_pos) for the order-preserving
    longest common subsequence of phonetic tokens between query and ref.

    ``ref_index`` (token -> ascending list of positions in ``ref``) may be
    supplied pre-built to avoid recomputing it per query window.
    """
    if not query or not ref:
        return 0, None, None
    if ref_index is None:
        ref_index = {}
        for pos, tok in enumerate(ref):
            ref_index.setdefault(tok, []).append(pos)

    positions: List[int] = []
    for tok in query:
        plist = ref_index.get(tok)
        if plist:
            # descending so a single query token can only extend the increasing
            # run with ONE of its occurrences (prevents self-overlap inflation)
            positions.extend(reversed(plist))
    if not positions:
        return 0, None, None
    lcs = _lis_length(positions)
    return lcs, min(positions), max(positions)


@dataclass
class FuzzyConfig:
    enabled: bool = True
    min_lcs_ratio: float = 0.45      # min LCS/len(query) to accept a fuzzy match
    min_query_tokens: int = 8        # below this the query is too short to trust
    weight: float = 1.0              # scale applied to fuzzy confidence
    # Order-preserving LCS is biased toward longer references (a longer episode
    # contains more of any token by chance) and toward common function words, so
    # on a short / noisy query the top two candidates can sit very close
    # together. ``min_margin`` requires the winner to beat the runner-up by this
    # confidence gap before the fuzzy result is trusted; ambiguous matches
    # (e.g. 0.87 vs 0.81) are rejected and the caller keeps the safer exact /
    # acoustic verdict instead of guessing.
    min_margin: float = 0.12

    @classmethod
    def from_config(cls, cfg: dict) -> "FuzzyConfig":
        fz = (cfg.get("matching", {}) or {}).get("fuzzy", {}) or {}
        return cls(
            enabled=fz.get("enabled", True),
            min_lcs_ratio=fz.get("min_lcs_ratio", 0.45),
            min_query_tokens=fz.get("min_query_tokens", 8),
            weight=fz.get("weight", 1.0),
            min_margin=fz.get("min_margin", 0.12),
        )


def score_fuzzy_matches(query_tokens: List[str],
                        candidate_streams: Dict[int, Tuple[MediaInfo, List[str], List[Optional[int]]]],
                        fuzzy_cfg: Optional[FuzzyConfig] = None,
                        top_n: int = 5) -> List[MatchResult]:
    """Rank candidates by order-preserving phonetic LCS against the query.

    Parameters
    ----------
    query_tokens
        Phonetic token stream of the unknown audio (see ``phonetic_token_stream``).
    candidate_streams
        Mapping ``media_id -> (MediaInfo, ref_tokens, ref_starts)``. Typically the
        handful of episodes shortlisted by the acoustic stage (loaded via
        ``FingerprintDB.get_token_stream``).
    fuzzy_cfg
        Tuning thresholds (see :class:`FuzzyConfig`).
    top_n
        Max results to return.

    Returns a list of :class:`MatchResult` sorted by confidence, where
    ``confidence = (LCS length / query length) * weight`` and ``match_count`` is
    the raw LCS length. The reported time window is derived from the cue start
    times of the first and last matched reference positions.
    """
    cfg = fuzzy_cfg or FuzzyConfig()
    q = list(query_tokens)
    if not q:
        return []
    qlen = len(q)

    results: List[MatchResult] = []
    for mid, (info, ref_tokens, ref_starts) in candidate_streams.items():
        if not ref_tokens:
            continue
        lcs, first_pos, last_pos = _fuzzy_lcs_length(q, ref_tokens)
        if lcs <= 0:
            continue
        confidence = min(1.0, (lcs / qlen) * cfg.weight)

        win_start = win_end = None
        if ref_starts:
            if first_pos is not None and first_pos < len(ref_starts):
                win_start = ref_starts[first_pos]
            if last_pos is not None and last_pos < len(ref_starts):
                win_end = ref_starts[last_pos]

        results.append(MatchResult(
            media=info, media_id=mid, confidence=confidence,
            match_count=lcs, query_count=qlen,
            window_start_ms=win_start, window_end_ms=win_end,
        ))

    results.sort(key=lambda r: (r.confidence, r.match_count), reverse=True)
    return results[:top_n]
