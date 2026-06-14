# Phonetic Audio Fingerprinting for TV & Movie Identification

A "Shazam for dialogue". Instead of matching audio waveforms, this system matches
**what is being said**. It builds a fingerprint database from subtitles (or audio),
then identifies live microphone audio (e.g. from your Plex/TV) by transcribing it,
encoding it phonetically, and matching shingled hashes against the database.

## How it works

```
text ──▶ clean / normalize ──▶ Double Metaphone phonetic encoding
     ──▶ 3-5 word phonetic shingles (sliding window)
     ──▶ stable hash per shingle ──▶ stored with timestamp + show metadata
```

Both sides — subtitles and spoken audio — go through the **identical** pipeline
(`fingerprint_core.fingerprint_text`), so a fuzzy speech-to-text transcript still
lines up with the clean subtitle text. The Double Metaphone step absorbs most STT
spelling/homophone errors ("their" vs "there", "objection" vs "objektion"), and
multiple shingle sizes (3, 4, 5) add robustness.

## Components

| File | Purpose |
|------|---------|
| `fingerprint_core.py` | Shared pipeline: text cleaning, Double Metaphone, shingling, hashing, SQLite DB, scoring |
| `subtitle_utils.py`   | `.srt`/`.vtt` parsing + OpenSubtitles.org download |
| `stt_utils.py`        | Speech-to-text engines (Vosk offline / Google online) |
| `create_fingerprint.py` | **Script 1** — fingerprint subtitles (download or local dir) |
| `identify_audio.py`     | **Script 2** — live microphone identification |
| `fingerprint_audio.py`  | **Script 3** — fingerprint / identify audio files on disk |
| `config.json`         | All tunable parameters |

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Install ffmpeg (required by pydub for audio)
```bash
# Debian/Ubuntu
sudo apt-get install ffmpeg
# macOS
brew install ffmpeg
```

### 3. Download a Vosk speech-to-text model (offline STT, recommended)
```bash
mkdir -p models && cd models
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
cd ..
```
The default `config.json` points at `models/vosk-model-small-en-us-0.15`.
Larger models (e.g. `vosk-model-en-us-0.22`) improve accuracy at the cost of speed.

> Prefer no model download? Set `"stt": { "engine": "google" }` in `config.json`
> to use SpeechRecognition's free online Google Web Speech endpoint (needs
> internet, rate-limited — fine for testing).

### 4. (Live mic only) Install PyAudio
```bash
sudo apt-get install portaudio19-dev   # Debian/Ubuntu
pip install pyaudio
```

## Usage

### Script 1 — Build the fingerprint database from subtitles

```bash
# Download "Matlock 1986" subtitles from OpenSubtitles and fingerprint them
python create_fingerprint.py --show "Matlock 1986"

# Or fingerprint a folder / single file of local subtitles
python create_fingerprint.py --dir ./subs --title "Matlock" --year 1986

# Inspect what's in the database
python create_fingerprint.py --list
```
Season/episode/year are auto-detected from filenames like
`Matlock.1986.S01E02.srt` or `Matlock 1x02.vtt`; override with `--title/--year/--type`.

#### OpenSubtitles access (important)

OpenSubtitles deprecated the old anonymous REST API. For reliable downloads:

1. Create a free account at <https://www.opensubtitles.com> and request a free
   API key at <https://www.opensubtitles.com/consumers>.
2. Put it in `config.json`:
   ```json
   "opensubtitles": {
     "provider": "auto",
     "api_key": "YOUR_API_KEY",
     "username": "your_user",   // optional, raises download quota
     "password": "your_pass"
   }
   ```
   With `provider: "auto"`, the official API is used when an `api_key` is present,
   otherwise it falls back to the legacy endpoint.

> The legacy `rest.opensubtitles.org` endpoint (no key) still exists but is
> frequently blocked behind Cloudflare or rate-limited, especially from cloud/
> datacenter IPs. If `--show` fails with 403/empty results, either add an API
> key or simply download `.srt` files from the website and use `--dir`.

### Script 2 — Identify live audio from the microphone

```bash
python identify_audio.py                # listen until Ctrl+C
python identify_audio.py --once         # stop at first confident match
python identify_audio.py --seconds 60   # stop after 60 seconds

# No microphone? Identify from a recorded clip instead:
python identify_audio.py --from-file clip.wav
```
Output example:
```
============================================================
>>> IDENTIFIED: Matlock (1986) S01E02
    confidence : 42.0%  (17 hash hits)
    approx time: 540s into source
    runners-up : Matlock (1986) S01E05 (8%)
============================================================
```

### Script 3 — Fingerprint / identify audio files on disk

```bash
# Add an episode's audio to the database
python fingerprint_audio.py --file ep1.mp3 --title "Matlock" --year 1986 \
       --season 1 --episode 1

# Batch-fingerprint a folder
python fingerprint_audio.py --dir /media/matlock --title "Matlock" --year 1986

# Identify a clip against the existing DB (don't store it)
python fingerprint_audio.py --file clip.wav --identify
```

## Configuration (`config.json`)

| Key | Meaning |
|-----|---------|
| `fingerprint.shingle_sizes` | Shingle word counts (default `[3,4,5]`) |
| `fingerprint.metaphone_primary_only` | Use only the primary Double Metaphone code |
| `matching.confidence_threshold` | Minimum confidence to report a match |
| `matching.time_window_seconds` | Window for the time-clustering confidence bonus |
| `matching.min_matches` | Minimum hash hits before a live match is announced |
| `audio.chunk_seconds` / `overlap_seconds` | Sliding-window size & overlap for STT |
| `stt.engine` | `vosk` (offline) or `google` (online) |
| `stt.vosk_model_path` | Path to the unzipped Vosk model |

## Output format

- **TV**: `Show Name (StartYear) SxxEyy` + confidence score
- **Movie**: `Movie Title (Year)` + confidence score

## Notes & limitations

- The database is plain SQLite (`fingerprints.db`) — portable and inspectable.
- Identification quality depends on STT quality; a larger Vosk model helps in
  noisy conditions. Background music/noise is partly mitigated by the rolling
  multi-window buffer and the time-clustering bonus.
- The OpenSubtitles downloader uses the public legacy REST search endpoint
  (no API key). If it is unavailable or rate-limited, download `.srt` files
  manually and use `--dir`.
- The VM here has no microphone; test the live path on your own machine, or use
  `--from-file` / `fingerprint_audio.py --identify` to validate end-to-end.

## Quick self-test (no model / no internet needed)

```bash
python selftest.py
```
This generates a tiny synthetic subtitle file, fingerprints it, and confirms a
transcript with deliberate STT-style errors still matches — exercising the full
phonetic pipeline and scoring.
```
