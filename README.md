# Phonetic Audio Fingerprinting for TV & Movie Identification

A "Shazam for dialogue". Instead of matching audio waveforms, this system matches\
**what is being said**. It builds a fingerprint database from subtitles (or audio),\
then identifies live microphone audio (e.g. from your Plex/TV) by transcribing it,\
encoding it phonetically, and matching shingled hashes against the database.

## How it works

```
text ──▶ clean / normalize ──▶ Double Metaphone phonetic encoding
     ──▶ 3-5 word phonetic shingles (sliding window)
     ──▶ stable hash per shingle ──▶ stored with timestamp + show metadata
```

Both sides — subtitles and spoken audio — go through the **identical** pipeline\
(`fingerprint_core.fingerprint_text`), so a fuzzy speech-to-text transcript still\
lines up with the clean subtitle text. The Double Metaphone step absorbs most STT\
spelling/homophone errors ("their" vs "there", "objection" vs "objektion"), and\
multiple shingle sizes (3, 4, 5) add robustness.

## Two fingerprinting methods (hybrid)

This system now combines **two complementary** identification methods:

1. **Phonetic** (dialogue) — matches *what is being said*. Great when characters
   are talking. Built from subtitles or speech-to-text.
2. **Acoustic** (sound) — matches *what it sounds like* using
   [Chromaprint/AcoustID](https://acoustid.org/chromaprint) fingerprints. Works
   during theme songs, music, sound effects and any non-dialogue scene where the
   phonetic method has nothing to hear.

When run in hybrid mode the system checks both and reports whichever has the
higher confidence, so you get the best of both worlds.

```
ACOUSTIC:  audio ──▶ ffmpeg slice ──▶ fpcalc (Chromaprint) ──▶ 32-bit frame ints
        ──▶ inverted index (exact sub-fingerprint) ──▶ offset voting
        ──▶ bit-error re-alignment ──▶ confidence = matched / query frames
```

## Components

| File | Purpose |
|------|---------|
| `fingerprint_core.py` | Shared config, SQLite DB (phonetic **and** acoustic tables), phonetic shingle pipeline, scoring |
| `acoustic_fingerprint.py` | Chromaprint generation, storage, and matching (the acoustic engine) |
| `create_fingerprint.py` | Build the **phonetic** DB from subtitles (`--show`/`--dir`); also `--file`/`--acoustic` to build acoustic fingerprints |
| `create_acoustic_fingerprint.py` | Build/identify the **acoustic** DB from audio/video files |
| `fingerprint_audio.py` | Fingerprint or identify audio files; `--acoustic` / `--both` for sound-based or hybrid |
| `identify_audio.py` | Live microphone identification; `--acoustic` / `--both` for sound-based or hybrid |
| `subtitle_utils.py` | Subtitle parsing + OpenSubtitles download |
| `stt_utils.py` | Speech-to-text helpers (Vosk / Google) |
| `selftest.py` | Quick end-to-end phonetic self-test |

## Acoustic fingerprinting — setup & usage

The acoustic engine needs the `fpcalc` tool from Chromaprint plus the
`pyacoustid` Python package:

```bash
# system tool (Debian/Ubuntu)
sudo apt-get install libchromaprint-tools     # provides 'fpcalc'
# macOS:  brew install chromaprint

# python package
pip install pyacoustid
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
python fingerprint_audio.py --file episode.mkv --acoustic   # acoustic only
python fingerprint_audio.py --file episode.mkv --both       # phonetic + acoustic
```

### Identify with acoustic / hybrid matching

```bash
# Identify a recorded clip by sound only
python create_acoustic_fingerprint.py --file clip.mp4 --identify

# Hybrid file identification (runs both, reports the more confident method)
python fingerprint_audio.py --file clip.mp4 --both --identify

# Live microphone, hybrid
python identify_audio.py --both

# Live microphone, acoustic only (no transcription needed)
python identify_audio.py --acoustic
```

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
| `fpcalc_path` | Path to the `fpcalc` binary (default `fpcalc`) |