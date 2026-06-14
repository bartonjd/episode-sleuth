"""
acoustic_fingerprint.py
=======================
Chromaprint / AcoustID acoustic fingerprinting.

This complements the phonetic (dialogue) fingerprinter: it identifies media from
the *sound itself* — theme music, sound effects, action scenes — so the system
keeps working even when nobody is speaking.

Pipeline
--------
    audio/video file ──▶ ffmpeg decode ──▶ Chromaprint fingerprint
                     ──▶ list of uint32 "frames" (~4-5 frames / second)

For storage we cut the audio into fixed SEGMENTS (default 30 s, with overlap) and
store each segment's raw Chromaprint frames as a BLOB, plus an inverted index of
the individual frame integers (sub-fingerprints) for fast candidate lookup.

For matching, a query clip's fingerprint is:
  1. looked up in the inverted index -> candidate (segment, offset) votes
     (classic offset-histogram alignment),
  2. refined by re-aligning the query against each candidate segment's raw
     fingerprint and counting the fraction of frames whose 32-bit Hamming
     distance is within `max_bit_error` -> a confidence in [0, 1].

The confidence is the fraction of query frames that match, directly comparable
to the phonetic matcher's "fraction of shingles matched".
"""

import os
import logging
import subprocess
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import acoustid
import chromaprint

from fingerprint_core import FingerprintDB, MediaInfo


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AcousticConfig:
    segment_seconds: int = 30
    overlap_seconds: int = 5
    fpcalc_path: str = "fpcalc"
    max_bit_error: int = 5          # max differing bits (of 32) to call a frame match
    align_window: int = 8           # +/- frames to search around the voted offset
    min_overlap_frames: int = 10    # minimum overlapping frames for a valid score
    confidence_threshold: float = 0.30
    index_stride: int = 1           # index every Nth frame
    query_chunk_seconds: int = 15   # query window size when identifying

    @classmethod
    def from_config(cls, cfg: dict) -> "AcousticConfig":
        ac = cfg.get("acoustic", {})
        return cls(
            segment_seconds=ac.get("segment_seconds", 30),
            overlap_seconds=ac.get("overlap_seconds", 5),
            fpcalc_path=ac.get("fpcalc_path", "fpcalc"),
            max_bit_error=ac.get("max_bit_error", 5),
            align_window=ac.get("align_window", 8),
            min_overlap_frames=ac.get("min_overlap_frames", 10),
            confidence_threshold=ac.get("confidence_threshold", 0.30),
            index_stride=ac.get("index_stride", 1),
            query_chunk_seconds=ac.get("query_chunk_seconds", 15),
        )


@dataclass
class AcousticMatchResult:
    media: MediaInfo
    media_id: int
    confidence: float
    matched_frames: int
    query_frames: int
    segment_index: Optional[int] = None
    ref_start_ms: Optional[int] = None
    method: str = "acoustic"

    def as_dict(self) -> dict:
        return {
            "title": self.media.title,
            "year": self.media.year,
            "label": self.media.label(),
            "confidence": round(self.confidence, 4),
            "matched_frames": self.matched_frames,
            "query_frames": self.query_frames,
            "method": self.method,
        }


# ---------------------------------------------------------------------------
# Fingerprint generation (Chromaprint via fpcalc / pyacoustid)
# ---------------------------------------------------------------------------

def _ensure_fpcalc(fpcalc_path: str) -> None:
    """Point pyacoustid at the configured fpcalc binary."""
    if fpcalc_path and fpcalc_path != "fpcalc":
        os.environ[acoustid.FPCALC_ENVVAR] = fpcalc_path


def generate_fingerprint(path: str, fpcalc_path: str = "fpcalc",
                         length: Optional[int] = None) -> Tuple[float, List[int]]:
    """Return (duration_seconds, raw_frames) for an audio/video file.

    `length` (seconds) optionally limits how much audio is analysed.
    Works on any format ffmpeg/Chromaprint can read (mp4, mkv, mp3, wav, ...).
    """
    _ensure_fpcalc(fpcalc_path)
    kwargs = {}
    if length:
        kwargs["maxlength"] = length
    duration, encoded = acoustid.fingerprint_file(path, **kwargs)
    raw, _algo = chromaprint.decode_fingerprint(encoded)
    return duration, list(raw)


def generate_segment_fingerprints(
        path: str, ac_cfg: AcousticConfig
) -> Tuple[float, List[Tuple[int, int, List[int]]]]:
    """Cut the file into overlapping segments and fingerprint each.

    Returns (total_duration_seconds, [(start_ms, end_ms, raw_frames), ...]).

    ffmpeg extracts each slice to a temporary 16 kHz mono wav which fpcalc then
    fingerprints. Slicing keeps per-segment timestamps so matches can report a
    location within the source.
    """
    import tempfile

    _ensure_fpcalc(ac_cfg.fpcalc_path)
    total = _probe_duration(path)
    if total <= 0:
        # fall back to a single whole-file fingerprint
        dur, raw = generate_fingerprint(path, ac_cfg.fpcalc_path)
        return dur, [(0, int(dur * 1000), raw)]

    seg_s = max(1, ac_cfg.segment_seconds)
    step_s = max(1, seg_s - ac_cfg.overlap_seconds)
    segments: List[Tuple[int, int, List[int]]] = []

    pos = 0.0
    idx = 0
    tmpdir = tempfile.mkdtemp(prefix="acfp_")
    try:
        while pos < total:
            length = min(seg_s, total - pos)
            if length < 1:
                break
            wav = os.path.join(tmpdir, f"seg_{idx}.wav")
            ok = _ffmpeg_extract(path, pos, length, wav)
            if ok:
                try:
                    _, encoded = acoustid.fingerprint_file(wav)
                    raw, _algo = chromaprint.decode_fingerprint(encoded)
                    if raw:
                        segments.append(
                            (int(pos * 1000), int((pos + length) * 1000), list(raw))
                        )
                except acoustid.FingerprintGenerationError as exc:
                    logging.warning("fingerprint failed for segment @%.0fs: %s", pos, exc)
                finally:
                    if os.path.exists(wav):
                        os.remove(wav)
            idx += 1
            if pos + length >= total:
                break
            pos += step_s
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return total, segments


def _probe_duration(path: str) -> float:
    """Get media duration in seconds using ffprobe (falls back to 0)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _ffmpeg_extract(path: str, start_s: float, length_s: float, out_wav: str) -> bool:
    """Extract a mono 16 kHz wav slice with ffmpeg. Returns True on success."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", f"{start_s:.3f}", "-t", f"{length_s:.3f}",
             "-i", path, "-ac", "1", "-ar", "16000", out_wav],
            capture_output=True, text=True, timeout=120,
        )
        return res.returncode == 0 and os.path.exists(out_wav)
    except subprocess.SubprocessError as exc:
        logging.warning("ffmpeg extract failed @%.0fs: %s", start_s, exc)
        return False


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def store_acoustic_fingerprints(db: FingerprintDB, info: MediaInfo, path: str,
                                ac_cfg: AcousticConfig, reindex: bool = True) -> int:
    """Generate segment fingerprints for `path` and store them under `info`.

    Returns the number of segments stored. Only acoustic data is touched, so a
    media item can hold both phonetic and acoustic fingerprints simultaneously.
    """
    logging.info("Generating acoustic fingerprint for %s ...", os.path.basename(path))
    total, segments = generate_segment_fingerprints(path, ac_cfg)
    if not segments:
        logging.warning("No acoustic fingerprint produced for %s", path)
        return 0

    if reindex:
        db.clear_media_acoustic(info)
    media_id = db.get_or_create_media(info)

    for seg_index, (start_ms, end_ms, raw) in enumerate(segments):
        db.add_acoustic_segment(media_id, seg_index, start_ms, end_ms,
                                raw, index_stride=ac_cfg.index_stride)
    logging.info("  + %-45s -> %3d segments, %.0fs",
                 info.label(), len(segments), total)
    return len(segments)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _popcount(x: int) -> int:
    return int(x).bit_count()


def _aligned_match(query: List[int], ref: List[int], offset: int,
                   max_bit_error: int) -> Tuple[int, int]:
    """Compare query against ref where query[i] aligns with ref[i+offset].

    Returns (matched_frames, overlap_frames).
    """
    matched = 0
    overlap = 0
    rlen = len(ref)
    for i, qv in enumerate(query):
        j = i + offset
        if 0 <= j < rlen:
            overlap += 1
            if _popcount(qv ^ ref[j]) <= max_bit_error:
                matched += 1
    return matched, overlap


def _best_alignment(query: List[int], ref: List[int], center_offset: int,
                    ac_cfg: AcousticConfig) -> Tuple[int, int, int]:
    """Search offsets around `center_offset` for the best match.

    Returns (matched_frames, overlap_frames, best_offset).
    """
    best = (0, 0, center_offset)
    lo = center_offset - ac_cfg.align_window
    hi = center_offset + ac_cfg.align_window
    for off in range(lo, hi + 1):
        matched, overlap = _aligned_match(query, ref, off, ac_cfg.max_bit_error)
        if overlap >= ac_cfg.min_overlap_frames and matched > best[0]:
            best = (matched, overlap, off)
    return best


def match_acoustic(query_raw: List[int], db: FingerprintDB,
                   ac_cfg: AcousticConfig, top_n: int = 5) -> List[AcousticMatchResult]:
    """Match a query Chromaprint fingerprint against the acoustic DB.

    Strategy: inverted-index offset voting to find candidate segments, then a
    precise bit-error re-alignment to score each candidate.
    """
    if not query_raw:
        return []

    # Map each query sub-fingerprint to the positions where it occurs.
    q_positions: Dict[int, List[int]] = {}
    for pos, sub in enumerate(query_raw):
        q_positions.setdefault(sub & 0xFFFFFFFF, []).append(pos)

    rows = db.lookup_acoustic_index(q_positions.keys())

    # Vote per (segment_id, offset). offset = ref_pos - query_pos.
    seg_votes: Dict[int, Dict[int, int]] = {}
    for r in rows:
        sub = r["subfp"] & 0xFFFFFFFF
        ref_pos = r["position"]
        seg_id = r["segment_id"]
        for q_pos in q_positions.get(sub, ()):  # usually 1
            offset = ref_pos - q_pos
            seg_votes.setdefault(seg_id, {})[offset] = \
                seg_votes.setdefault(seg_id, {}).get(offset, 0) + 1

    if not seg_votes:
        return []

    # For each candidate segment, take the best-voted offset, then re-align
    # precisely against the stored raw fingerprint.
    per_media: Dict[int, AcousticMatchResult] = {}
    for seg_id, offsets in seg_votes.items():
        center = max(offsets, key=offsets.get)
        seg = db.get_acoustic_segment(seg_id)
        if not seg or not seg["raw"]:
            continue
        matched, overlap, best_off = _best_alignment(
            query_raw, seg["raw"], center, ac_cfg)
        if overlap < ac_cfg.min_overlap_frames:
            continue
        confidence = matched / max(1, len(query_raw))

        info = MediaInfo(title=seg["title"], year=seg["year"],
                         media_type=seg["media_type"], season=seg["season"],
                         episode=seg["episode"])
        prev = per_media.get(seg["media_id"])
        if prev is None or confidence > prev.confidence:
            per_media[seg["media_id"]] = AcousticMatchResult(
                media=info, media_id=seg["media_id"], confidence=confidence,
                matched_frames=matched, query_frames=len(query_raw),
                segment_index=seg["segment_index"], ref_start_ms=seg["start_ms"],
            )

    results = sorted(per_media.values(),
                     key=lambda r: r.confidence, reverse=True)
    return results[:top_n]


def identify_file_acoustic(path: str, db: FingerprintDB, ac_cfg: AcousticConfig,
                           top_n: int = 5) -> List[AcousticMatchResult]:
    """Identify an audio/video file using acoustic fingerprints.

    The file is fingerprinted in query-chunk windows; the best-scoring chunk's
    ranking is returned.
    """
    total, segments = generate_segment_fingerprints(
        path,
        AcousticConfig(segment_seconds=ac_cfg.query_chunk_seconds,
                       overlap_seconds=min(ac_cfg.overlap_seconds,
                                           ac_cfg.query_chunk_seconds - 1),
                       fpcalc_path=ac_cfg.fpcalc_path,
                       max_bit_error=ac_cfg.max_bit_error,
                       align_window=ac_cfg.align_window,
                       min_overlap_frames=ac_cfg.min_overlap_frames,
                       index_stride=ac_cfg.index_stride),
    )
    if not segments:
        # whole-file fallback
        _, raw = generate_fingerprint(path, ac_cfg.fpcalc_path)
        return match_acoustic(raw, db, ac_cfg, top_n)

    best_by_media: Dict[int, AcousticMatchResult] = {}
    for (_s, _e, raw) in segments:
        for res in match_acoustic(raw, db, ac_cfg, top_n):
            prev = best_by_media.get(res.media_id)
            if prev is None or res.confidence > prev.confidence:
                best_by_media[res.media_id] = res
    return sorted(best_by_media.values(),
                  key=lambda r: r.confidence, reverse=True)[:top_n]


def match_acoustic_pcm(pcm_bytes: bytes, sample_rate: int, channels: int,
                       db: FingerprintDB, ac_cfg: AcousticConfig,
                       top_n: int = 5) -> List[AcousticMatchResult]:
    """Match raw PCM (e.g. from a live microphone window) against the DB."""
    _ensure_fpcalc(ac_cfg.fpcalc_path)
    try:
        encoded, _dur = _fingerprint_pcm(pcm_bytes, sample_rate, channels)
        raw, _algo = chromaprint.decode_fingerprint(encoded)
    except Exception as exc:
        logging.debug("acoustic PCM fingerprint failed: %s", exc)
        return []
    return match_acoustic(list(raw), db, ac_cfg, top_n)


def _fingerprint_pcm(pcm_bytes: bytes, sample_rate: int, channels: int):
    """Fingerprint raw 16-bit PCM using pyacoustid (Chromaprint).

    `acoustid.fingerprint` takes an iterable of PCM byte blocks and returns the
    encoded fingerprint bytes.
    """
    encoded = acoustid.fingerprint(sample_rate, channels, iter([pcm_bytes]))
    duration = len(pcm_bytes) / (2 * channels * sample_rate)
    return encoded, duration
