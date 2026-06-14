# Phonetic Audio Fingerprinting for TV & Movie Identification

A "Shazam for dialogue". Instead of matching audio waveforms only, this system can match\
**what is being said**. It builds a reference database from **subtitles** (phonetic) and\
**audio waveforms** (acoustic), then identifies live microphone audio (e.g. from your\
Plex/TV) by transcribing it and/or fingerprinting its sound, and matching against the database.

### Workflow at a glance

```
BUILD REFERENCE DATABASE (no transcription needed):
  subtitles (.srt/.vtt)  ──▶ phonetic fingerprints   (create_fingerprint.py)
  audio / video files    ──▶ acoustic fingerprints    (create_acoustic_fingerprint.py
                                                        or fingerprint_audio.py)

IDENTIFY UNKNOWN AUDIO (transcription happens here, and only here):
  unknown audio ──▶ transcribe (STT) ──▶ phonetic match  ┐
  unknown audio ──▶ Chromaprint        ──▶ acoustic match ┴▶ best result  (identify_audio.py)
```

Because we already have accurate dialogue in the subtitles, **reference media is never
transcribed**. Speech-to-text is used exclusively when identifying an unknown clip.

## How it works

```
text ──▶ clean / normalize ──▶ Double Metaphone phonetic encoding
     ──▶ 3-5 word phonetic shingles (sliding window)
     ──▶ stable hash per shingle ──▶ stored with timestamp + show metadata
```

Both sides — the reference **subtitles** and the **transcribed unknown audio** at
identification time — go through the **identical** pipeline
(`fingerprint_core.fingerprint_text`), so a fuzzy speech-to-text transcript still\
lines up with the clean subtitle text. The Double Metaphone step absorbs most STT\
spelling/homophone errors ("their" vs "there", "objection" vs "objektion"), and\
multiple shingle sizes (3, 4, 5) add robustness.

## Two fingerprinting methods (hybrid)

This system now combines **two complementary** identification methods:

1. **Phonetic** (dialogue) — matches *what is being said*. Great when characters
   are talking. The reference database is built **from subtitles**; at
   identification time, unknown audio is transcribed (STT) and matched against it.
2. **Acoustic** (sound) — matches *what it sounds like* using
   [Chromaprint/AcoustID](https://acoustid.org/chromaprint) fingerprints. Works
   during theme songs, music, sound effects and any non-dialogue scene where the
   phonetic method has nothing to hear.

```
ACOUSTIC:  audio ──▶ ffmpeg slice ──▶ fpcalc (Chromaprint) ──▶ 32-bit frame ints
        ──▶ inverted index (exact sub-fingerprint) ──▶ offset voting
        ──▶ bit-error re-alignment ──▶ confidence = matched / query frames
```

### ⭐ Recommended: two-stage hybrid mode (`--hybrid`)

For a true Shazam-like experience use **`--hybrid`**, an intelligent two-stage
pipeline that is both **faster** and **more accurate** than running either method
alone:

```
STAGE 1 — ACOUSTIC SHORTLIST (fast, noise-tolerant)
  unknown audio ──▶ Chromaprint ──▶ rank reference media
                ──▶ keep top-N candidate episodes  (default 5)

STAGE 2 — SCOPED PHONETIC CONFIRMATION (precise)
  unknown audio ──▶ transcribe (STT) ──▶ EXACT shingle-hash match
                ──▶ *restricted to the shortlisted candidates only*
                ──▶ final, confident episode

STAGE 2b — FUZZY PHONETIC FALLBACK (only if Stage 2 is weak)
  noisy transcript ──▶ order-preserving phonetic LCS
                   ──▶ tolerates dropped / mis-heard / inserted words
                   ──▶ margin-gated so ambiguous near-ties are rejected
```

Why this is better:

* **Speed** — the phonetic fingerprint table is huge (one row per dialogue
  shingle for the whole library). Restricting the search to ~5 acoustically
  shortlisted episodes instead of the entire library makes the phonetic lookup
  **~10x faster** on a large library (measured 20.4 ms → 1.8 ms on a 1,500-episode
  / 11.6 M-fingerprint synthetic library), with no loss of accuracy.
* **Robustness** — Stage 1 uses a *recall-focused* acoustic configuration (a more
  forgiving Hamming-distance tolerance, `candidate_max_bit_error`) so the correct
  episode survives into the shortlist even for noisy microphone captures or lossy
  re-encodes. Stage 2's phonetic precision then nails the exact episode.
* **Safety net** — if the acoustic stage returns no candidates, Stage 2
  automatically widens to a full-database phonetic search, so identification
  still succeeds.
* **Fuzzy fallback (Stage 2b)** — exact shingle hashing only ever counts
  *identical* hashes, so a single mis-heard / dropped word from the STT engine
  breaks every shingle that overlaps it. When the exact result is missing or
  below `phonetic_confirm_threshold`, the hybrid runs an **order-preserving
  phonetic LCS** matcher instead (see below), which degrades gracefully on noisy
  microphone transcripts.

The older `--both` mode (run both methods independently over the whole database
and report the more confident) is still available, but `--hybrid` is the
recommended default.

#### Fuzzy (order-preserving) phonetic matching

The exact matcher hashes 3/4/5 consecutive phonetic tokens into one opaque
value, so it is precise but brittle: one STT error destroys up to N shingles per
size. The fuzzy matcher instead keeps the raw **ordered stream of phonetic
tokens** for every reference (stored in the `media_tokens` table) and scores a
candidate by the **Longest Common Subsequence (LCS)** of tokens shared, *in the
same order*, with the query:

* a **dropped** query word merely shortens the subsequence,
* an **inserted** word is skipped over (it becomes a gap),
* a **mis-heard** word contributes nothing but its neighbours still align,

so the score falls gracefully instead of off a cliff. LCS length is computed in
`O(M log M)` via the classic *LCS-as-Longest-Increasing-Subsequence* trick (map
each query token to its reference positions in descending order, take the LIS),
and `confidence = LCS length / query length`.

Because LCS is biased toward **longer references** (a bigger episode contains
more of any token by chance) and toward **common function words**, the fuzzy
result is only trusted when the top candidate beats the runner-up by
`min_margin` — ambiguous near-ties (e.g. 0.87 vs 0.81) are rejected so the
system keeps the safer exact/acoustic verdict rather than guessing. This is why
fuzzy is a *fallback*, never an override of a confident exact match.

The reference token streams are built automatically by `create_fingerprint.py`
alongside the shingle hashes — no extra step is required.

## Components

| File | Purpose |
|------|---------|
| `fingerprint_core.py` | Shared config, SQLite DB (phonetic **and** acoustic tables), phonetic shingle pipeline, scoring |
| `acoustic_fingerprint.py` | Chromaprint generation, storage, and matching (the acoustic engine) |
| `create_fingerprint.py` | Build the **phonetic** DB from subtitles (`--show`/`--dir`); also `--file`/`--acoustic` to build acoustic fingerprints. No transcription. |
| `create_acoustic_fingerprint.py` | Build/identify the **acoustic** DB from audio/video files (Chromaprint only) |
| `fingerprint_audio.py` | Build **acoustic** fingerprints from audio/video files (Chromaprint only — no transcription) |
| `identify_audio.py` | Identify unknown audio (mic or file). **`--hybrid`** (recommended) = acoustic shortlist → scoped phonetic confirm; also `--acoustic`, `--both`, or phonetic-only (default) |
| `subtitle_utils.py` | Subtitle parsing + OpenSubtitles download |
| `stt_utils.py` | Speech-to-text helpers (Vosk / Google) — used **only** by `identify_audio.py` |
| `selftest.py` | Quick end-to-end phonetic self-test |

## Acoustic fingerprinting — setup & usage

The acoustic engine calls the `fpcalc` command-line tool from Chromaprint
**directly** — there is no Python `chromaprint`/`pyacoustid` binding to install,
so it works on Windows as soon as `fpcalc.exe` is present.

```bash
# system tool only — no Python package needed
sudo apt-get install libchromaprint-tools     # Debian/Ubuntu (provides 'fpcalc')
# macOS:    brew install chromaprint
# Windows:  see INSTALL_WINDOWS.md (or run install_chromaprint.ps1)
```

If `fpcalc` is installed somewhere not on your `PATH`, point the project at it
via `config.json`:

```json
"acoustic": { "fpcalc_path": "C:\\Tools\\chromaprint\\fpcalc.exe" }
```

### Build an acoustic database

```bash
# Single episode (metadata from flags)
python create_acoustic_fingerprint.py --file "Matlock.S01E03.mkv" \
       --title "Matlock" --year 1986 --season 1 --episode 3

# A whole folder (metadata auto-detected from filenames)
python create_acoustic_fingerprint.py --dir /media/matlock --title "Matlock"

# List media that have acoustic fingerprints
python create_acoustic_fingerprint.py --list
```

You can also build acoustic fingerprints through the other tools:

```bash
python create_fingerprint.py --file episode.mkv          # acoustic (single file)
python create_fingerprint.py --dir /media --acoustic     # acoustic (folder)
python fingerprint_audio.py --file episode.mkv           # acoustic (single file)
python fingerprint_audio.py --dir /media                 # acoustic (folder)
```

> Note: `fingerprint_audio.py` is **acoustic-only** — it never transcribes
> reference media. To add *phonetic* (dialogue) fingerprints, use subtitles with
> `create_fingerprint.py`.

### Identify with hybrid / acoustic / phonetic matching

Transcription only happens here — when identifying **unknown** audio.

```bash
# ⭐ RECOMMENDED — two-stage hybrid: acoustic shortlist + scoped phonetic confirm
python identify_audio.py --from-file clip.mp4 --hybrid

# ⭐ RECOMMENDED — same, live from the microphone
python identify_audio.py --hybrid

# Stop on first confident hit (handy for live use)
python identify_audio.py --hybrid --once

# --both: run phonetic AND acoustic independently over the whole DB, pick the winner
python identify_audio.py --from-file clip.mp4 --both

# Phonetic-only file identification (transcribe + match against subtitle DB)
python identify_audio.py --from-file clip.mp4

# Acoustic only (no transcription needed)
python identify_audio.py --from-file clip.mp4 --acoustic

# Identify a recorded clip by sound only (via the acoustic helper)
python create_acoustic_fingerprint.py --file clip.mp4 --identify
```

Example `--hybrid` output:

```
[Stage 1] Acoustic shortlist  (1.19s)  -> 3 candidate episode(s) (6 media row(s) to confirm):
    1. Matlock (1986) (1986) S01E01           acoustic=16.5%
    2. Matlock (1986) (1986) S01E03           acoustic=14.3%
    3. Matlock (1986) (1986) S01E04           acoustic=12.1%

[Stage 2] Scoped phonetic     (2.08s)  -> searched 6 episode(s):
  >> 1. Matlock (1986) (1986) S01E04           phonetic=27.6% (1 hits)

>>> IDENTIFIED: Matlock (1986) (1986) S01E04  (27.6%)
    method: hybrid (acoustic shortlist + phonetic confirm)
```

> Note how Stage 1's *acoustic* ranking alone would have been wrong (it ranked
> S01E01 first for this noisy re-recording), but Stage 2's *phonetic* confirmation
> within the shortlist correctly pins **S01E04** — exactly why the hybrid is more
> accurate than acoustic alone, and faster than scanning the whole phonetic DB.

### Acoustic configuration (`config.json` → `"acoustic"`)

| Key | Meaning |
|-----|---------|
| `segment_seconds` | Length of each stored reference segment (default 30) |
| `overlap_seconds` | Overlap between consecutive segments (default 5) |
| `query_chunk_seconds` | Window length used when identifying (default 15) |
| `max_bit_error` | Max differing bits (of 32) to count two frames as matching |
| `align_window` | +/- frames searched around the voted offset during re-alignment |
| `min_overlap_frames` | Minimum overlapping frames for a valid score |
| `confidence_threshold` | Minimum confidence to report an acoustic match (default 0.30) |
| `brute_force_fallback` | If the exact-match index finds nothing, scan all segments with bit tolerance (default `true`) |
| `brute_force_max_segments` | Skip the brute-force scan above this many segments (perf guard, default 8000) |
| `candidate_max_bit_error` | **Recall-focused** bit tolerance used only when building the hybrid shortlist (default 9, more forgiving than `max_bit_error`) |
| `candidate_min_overlap_frames` | Minimum overlap for a shortlist candidate (default 8) |
| `fpcalc_path` | Path to the `fpcalc` binary (default `fpcalc`) |

### Hybrid configuration (`config.json` → `"hybrid"`)

| Key | Meaning |
|-----|---------|
| `top_candidates_count` | How many episodes the acoustic stage shortlists for phonetic confirmation (default 5) |
| `acoustic_shortlist_threshold` | Drop shortlist candidates below this acoustic confidence; `0.0` keeps all top-N (default 0.0) |
| `phonetic_confirm_threshold` | Minimum phonetic confidence for the final hybrid verdict (default 0.15) |

### Fuzzy matching configuration (`config.json` → `"matching"."fuzzy"`)

The order-preserving phonetic LCS fallback (Stage 2b). It only runs when the
exact phonetic result is missing or below `phonetic_confirm_threshold`.

| Key | Meaning |
|-----|---------|
| `enabled` | Turn the fuzzy fallback on/off (default `true`) |
| `min_lcs_ratio` | Minimum `LCS length / query tokens` to accept a fuzzy match (default 0.45) |
| `min_query_tokens` | Skip fuzzy when the query has fewer phonetic tokens than this — too little signal to trust (default 8) |
| `min_margin` | The top candidate must beat the runner-up by this confidence gap, else the match is treated as ambiguous and rejected (default 0.12) |
| `weight` | Scale applied to the raw fuzzy confidence before thresholding (default 1.0) |