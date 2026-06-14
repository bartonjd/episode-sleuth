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
import json
import wave
import logging
import tempfile
import subprocess
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Iterable

from fingerprint_core import FingerprintDB, MediaInfo


class FpcalcNotFoundError(RuntimeError):
    """Raised when the Chromaprint ``fpcalc`` executable cannot be run.

    The message includes platform-specific install hints so the failure is
    actionable for the user (especially on Windows, where the Python
    ``chromaprint`` bindings are usually unavailable and only ``fpcalc.exe``
    is installed).
    """


_FPCALC_INSTALL_HINT = (
    "fpcalc is part of Chromaprint and is required for acoustic fingerprinting.\n"
    "  - Windows:        see INSTALL_WINDOWS.md (or run install_chromaprint.ps1)\n"
    "  - Debian/Ubuntu:  sudo apt-get install libchromaprint-tools\n"
    "  - macOS (brew):   brew install chromaprint\n"
    "If fpcalc is installed in a non-standard location, set "
    "\"acoustic\": {\"fpcalc_path\": \"...\"} in config.json."
)

# Cache of fpcalc paths we have already verified, so we only probe once.
_FPCALC_OK: Dict[str, bool] = {}


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
    # When the exact-match inverted index finds no candidate segments (which is
    # what happens for any lossy / re-encoded / re-recorded audio, where the
    # 32-bit sub-fingerprints rarely match a reference frame *exactly*), fall
    # back to a bit-tolerant brute-force scan over every stored segment. This is
    # only enabled when the reference DB is small enough to scan quickly.
    brute_force_fallback: bool = True
    brute_force_max_segments: int = 8000
    # Recall-focused bit tolerance used ONLY when building a candidate shortlist
    # for the two-stage hybrid workflow. Real-world microphone / re-recorded
    # audio is noisy, so the strict `max_bit_error` (good for precise scoring)
    # can rank the true episode just below noise. For shortlisting we only need
    # the correct episode to appear among the top-N candidates, so we use a more
    # forgiving threshold here to maximise recall; the precise phonetic stage
    # then disambiguates.
    candidate_max_bit_error: int = 9
    candidate_min_overlap_frames: int = 8

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
            brute_force_fallback=ac.get("brute_force_fallback", True),
            brute_force_max_segments=ac.get("brute_force_max_segments", 8000),
            candidate_max_bit_error=ac.get("candidate_max_bit_error", 9),
            candidate_min_overlap_frames=ac.get("candidate_min_overlap_frames", 8),
        )

    def recall_variant(self) -> "AcousticConfig":
        """Return a copy tuned for high-recall candidate shortlisting.

        Used by the hybrid workflow's acoustic pre-filter: it relaxes the bit
        tolerance and overlap requirement so the true episode is more likely to
        survive into the shortlist that the phonetic stage then confirms.
        """
        from dataclasses import replace
        return replace(
            self,
            max_bit_error=self.candidate_max_bit_error,
            min_overlap_frames=self.candidate_min_overlap_frames,
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
# Fingerprint generation (Chromaprint via the fpcalc command-line tool)
# ---------------------------------------------------------------------------
#
# We invoke the `fpcalc` binary directly instead of the Python `chromaprint`
# bindings. The bindings are frequently missing on Windows (and raise
# "module 'chromaprint' has no attribute 'Fingerprinter'"), whereas fpcalc.exe
# is a single self-contained download. We use `-raw -json`, which prints
#     {"duration": <seconds>, "fingerprint": [<uint32>, <uint32>, ...]}
# so no separate decode step is needed.

def check_fpcalc(fpcalc_path: str = "fpcalc") -> str:
    """Verify that the fpcalc executable can be run; return its path.

    Raises :class:`FpcalcNotFoundError` with actionable install instructions if
    fpcalc is missing or cannot be executed. The result is cached per path so
    repeated calls are cheap.
    """
    if _FPCALC_OK.get(fpcalc_path):
        return fpcalc_path
    try:
        subprocess.run([fpcalc_path, "-version"],
                       capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise FpcalcNotFoundError(
            f"Could not find the 'fpcalc' tool (looked for: {fpcalc_path!r}).\n"
            + _FPCALC_INSTALL_HINT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FpcalcNotFoundError(
            f"The 'fpcalc' tool ({fpcalc_path!r}) could not be run: {exc}\n"
            + _FPCALC_INSTALL_HINT
        )
    _FPCALC_OK[fpcalc_path] = True
    return fpcalc_path


# Backwards-compatible alias (older callers may import this name).
def _ensure_fpcalc(fpcalc_path: str) -> None:
    check_fpcalc(fpcalc_path)


def _run_fpcalc_raw(path: str, fpcalc_path: str = "fpcalc",
                    length: Optional[int] = None) -> Tuple[float, List[int]]:
    """Run ``fpcalc -raw -json`` on a file and parse the result.

    Returns ``(duration_seconds, [raw_uint32_frames])``.

    NOTE: fpcalc often exits with a non-zero status even on success (it emits a
    harmless "Error decoding audio frame (End of file)" warning at EOF), so we
    judge success by whether valid JSON with a fingerprint was produced on
    stdout rather than by the return code.
    """
    cmd = [fpcalc_path, "-raw", "-json"]
    if length:
        cmd += ["-length", str(int(length))]
    cmd.append(path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise FpcalcNotFoundError(
            f"Could not find the 'fpcalc' tool (looked for: {fpcalc_path!r}).\n"
            + _FPCALC_INSTALL_HINT
        )

    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()
        raise RuntimeError(
            f"fpcalc produced no output for {path!r} (exit {proc.returncode}): "
            f"{err[:200] or 'no stderr'}"
        )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse fpcalc JSON for {path!r}: {exc}; "
            f"output starts with {out[:120]!r}"
        )

    raw = data.get("fingerprint") or []
    duration = float(data.get("duration") or 0.0)
    # fpcalc already emits unsigned 32-bit values; mask defensively so every
    # value fits the uint32 storage/index format.
    return duration, [int(x) & 0xFFFFFFFF for x in raw]


def generate_fingerprint(path: str, fpcalc_path: str = "fpcalc",
                         length: Optional[int] = None) -> Tuple[float, List[int]]:
    """Return (duration_seconds, raw_frames) for an audio/video file.

    `length` (seconds) optionally limits how much audio is analysed.
    Works on any format ffmpeg/Chromaprint can read (mp4, mkv, mp3, wav, ...).
    """
    check_fpcalc(fpcalc_path)
    return _run_fpcalc_raw(path, fpcalc_path, length)


def generate_segment_fingerprints(
        path: str, ac_cfg: AcousticConfig
) -> Tuple[float, List[Tuple[int, int, List[int]]]]:
    """Cut the file into overlapping segments and fingerprint each.

    Returns (total_duration_seconds, [(start_ms, end_ms, raw_frames), ...]).

    ffmpeg extracts each slice to a temporary 16 kHz mono wav which fpcalc then
    fingerprints. Slicing keeps per-segment timestamps so matches can report a
    location within the source.
    """
    check_fpcalc(ac_cfg.fpcalc_path)
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
                    _, raw = _run_fpcalc_raw(wav, ac_cfg.fpcalc_path)
                    if raw:
                        segments.append(
                            (int(pos * 1000), int((pos + length) * 1000), raw)
                        )
                except FpcalcNotFoundError:
                    raise
                except Exception as exc:
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


def file_already_acoustic_fingerprinted(db: FingerprintDB, path: str) -> bool:
    """Return True if `path` already has acoustic fingerprints in the DB.

    Used for smart-skip: media is keyed by its source file path, so we look up
    whether any acoustic segments exist for that path.
    """
    return db.file_has_acoustic(path)


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


def _full_scan_alignment(query: List[int], ref: List[int],
                         ac_cfg: AcousticConfig) -> Tuple[int, int, int]:
    """Slide `query` across the whole of `ref` and return the best alignment.

    Unlike :func:`_best_alignment` (which only searches a small window around a
    voted offset), this scans *every* offset with enough overlap. It is the
    workhorse of the brute-force fallback used when the exact-match inverted
    index produced no candidates.

    Returns (matched_frames, overlap_frames, best_offset).
    """
    qlen = len(query)
    rlen = len(ref)
    if qlen == 0 or rlen == 0:
        return (0, 0, 0)
    min_ov = ac_cfg.min_overlap_frames
    max_bit = ac_cfg.max_bit_error
    best = (0, 0, 0)
    # Offsets such that at least `min_ov` query frames overlap the reference.
    lo = -(qlen - min_ov)
    hi = rlen - min_ov
    for off in range(lo, hi + 1):
        i0 = max(0, -off)
        i1 = min(qlen, rlen - off)
        overlap = i1 - i0
        if overlap < min_ov:
            continue
        matched = 0
        for i in range(i0, i1):
            if _popcount(query[i] ^ ref[i + off]) <= max_bit:
                matched += 1
        if matched > best[0]:
            best = (matched, overlap, off)
    return best


def _brute_force_match(query_raw: List[int], db: FingerprintDB,
                       ac_cfg: AcousticConfig, top_n: int) -> List[AcousticMatchResult]:
    """Bit-tolerant scan over every stored segment.

    Used as a fallback when the exact-match inverted index yields no candidate
    segments. This is necessary for any audio that is not a bit-identical copy
    of the reference (lossy re-encodes, re-recordings, microphone captures),
    because Chromaprint sub-fingerprints of such audio almost never match a
    reference frame *exactly* and therefore never appear in the inverted index.
    """
    seg_ids = db.all_acoustic_segment_ids()
    if not seg_ids or len(seg_ids) > ac_cfg.brute_force_max_segments:
        if seg_ids:
            logging.debug(
                "acoustic brute-force skipped: %d segments exceeds limit %d",
                len(seg_ids), ac_cfg.brute_force_max_segments)
        return []

    logging.debug("acoustic: no exact index hits; brute-force scanning %d segments",
                  len(seg_ids))
    per_media: Dict[int, AcousticMatchResult] = {}
    qlen = len(query_raw)
    for seg_id in seg_ids:
        seg = db.get_acoustic_segment(seg_id)
        if not seg or not seg["raw"]:
            continue
        matched, overlap, _off = _full_scan_alignment(query_raw, seg["raw"], ac_cfg)
        if overlap < ac_cfg.min_overlap_frames or matched == 0:
            continue
        confidence = matched / max(1, qlen)
        info = MediaInfo(title=seg["title"], year=seg["year"],
                         media_type=seg["media_type"], season=seg["season"],
                         episode=seg["episode"])
        prev = per_media.get(seg["media_id"])
        if prev is None or confidence > prev.confidence:
            per_media[seg["media_id"]] = AcousticMatchResult(
                media=info, media_id=seg["media_id"], confidence=confidence,
                matched_frames=matched, query_frames=qlen,
                segment_index=seg["segment_index"], ref_start_ms=seg["start_ms"],
            )
    return sorted(per_media.values(),
                  key=lambda r: r.confidence, reverse=True)[:top_n]


def match_acoustic(query_raw: List[int], db: FingerprintDB,
                   ac_cfg: AcousticConfig, top_n: int = 5) -> List[AcousticMatchResult]:
    """Match a query Chromaprint fingerprint against the acoustic DB.

    Strategy: inverted-index offset voting to find candidate segments, then a
    precise bit-error re-alignment to score each candidate. If the (exact-match)
    index yields no candidates, fall back to a bit-tolerant brute-force scan so
    that lossy / re-recorded audio can still be matched.
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
        # No segment shared an exact sub-fingerprint with the query. This is the
        # normal case for lossy / re-recorded audio. Fall back to a bit-tolerant
        # brute-force scan (when the DB is small enough) instead of giving up.
        if ac_cfg.brute_force_fallback:
            return _brute_force_match(query_raw, db, ac_cfg, top_n)
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


def shortlist_candidates(path: str, db: FingerprintDB, ac_cfg: AcousticConfig,
                         top_n: int = 5) -> List[AcousticMatchResult]:
    """Stage 1 of the hybrid workflow: acoustically shortlist candidate media.

    Returns up to ``top_n`` :class:`AcousticMatchResult`, ordered best-first,
    using the *recall-focused* acoustic configuration (more forgiving bit
    tolerance) so the true episode is likely to be present even for noisy
    microphone captures. The caller then runs precise phonetic matching scoped
    to ``[r.media_id for r in results]``.
    """
    recall_cfg = ac_cfg.recall_variant()
    return identify_file_acoustic(path, db, recall_cfg, top_n=top_n)


def shortlist_candidates_pcm(pcm_bytes: bytes, sample_rate: int, channels: int,
                             db: FingerprintDB, ac_cfg: AcousticConfig,
                             top_n: int = 5) -> List[AcousticMatchResult]:
    """Live-capture variant of :func:`shortlist_candidates` (raw PCM input)."""
    recall_cfg = ac_cfg.recall_variant()
    return match_acoustic_pcm(pcm_bytes, sample_rate, channels, db,
                              recall_cfg, top_n=top_n)


def match_acoustic_pcm(pcm_bytes: bytes, sample_rate: int, channels: int,
                       db: FingerprintDB, ac_cfg: AcousticConfig,
                       top_n: int = 5) -> List[AcousticMatchResult]:
    """Match raw PCM (e.g. from a live microphone window) against the DB."""
    check_fpcalc(ac_cfg.fpcalc_path)
    try:
        raw = _fingerprint_pcm(pcm_bytes, sample_rate, channels,
                               ac_cfg.fpcalc_path)
    except FpcalcNotFoundError:
        raise
    except Exception as exc:
        logging.debug("acoustic PCM fingerprint failed: %s", exc)
        return []
    return match_acoustic(raw, db, ac_cfg, top_n)


def _fingerprint_pcm(pcm_bytes: bytes, sample_rate: int, channels: int,
                     fpcalc_path: str = "fpcalc") -> List[int]:
    """Fingerprint raw 16-bit PCM by writing a temporary WAV for fpcalc.

    fpcalc reads files, not stdin PCM, so we wrap the PCM samples in a minimal
    WAV container (16-bit little-endian, matching pydub/PyAudio paInt16) and run
    fpcalc on it. Returns the list of raw uint32 frames.
    """
    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="acpcm_")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)            # 16-bit PCM
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        _duration, raw = _run_fpcalc_raw(wav_path, fpcalc_path)
        return raw
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
