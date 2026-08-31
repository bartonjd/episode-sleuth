# Phonetic Audio Fingerprinting for TV & Movie Identification

A "Shazam for dialogue". This system matches **what is being said** by building
a reference database from **subtitles**, then identifying unknown audio (e.g. a
DVD rip or a live TV recording) by transcribing it and matching the dialogue
against the database.

### Workflow at a glance

```
BUILD REFERENCE DATABASE (no transcription needed):
  subtitles (.srt/.vtt)  --> phonetic fingerprints   (create_fingerprint.py)

IDENTIFY UNKNOWN AUDIO (transcription happens here, and only here):
  unknown audio --> transcribe (STT) --> phonetic match --> best result
```

Because we already have accurate dialogue in the subtitles, **reference media is
never transcribed**. Speech-to-text is used exclusively when identifying an
unknown clip.

## How it works

```
text --> clean / normalize --> Double Metaphone phonetic encoding
     --> 3-5 word phonetic shingles (sliding window)
     --> stable hash per shingle --> stored with timestamp + show metadata
```

Both sides - the reference **subtitles** and the **transcribed unknown audio** at
identification time - go through the **identical** pipeline
(`fingerprint_core.fingerprint_text`), so a fuzzy speech-to-text transcript still
lines up with the clean subtitle text. The Double Metaphone step absorbs most STT
spelling/homophone errors ("their" vs "there", "objection" vs "objektion"), and
multiple shingle sizes (3, 4, 5) add robustness.

## Phonetic matching

The system uses two complementary strategies within the phonetic approach:

### Exact shingle-hash matching

Each subtitle (or transcribed clip) is broken into overlapping shingles of 3, 4,
and 5 phonetic tokens, hashed, and stored. At identification time the unknown
audio is transcribed, shingled identically, and matched by hash lookup. This is
fast and precise - any exact dialogue overlap lights up immediately.

### Fuzzy (order-preserving) phonetic matching

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
`min_margin` - ambiguous near-ties (e.g. 0.87 vs 0.81) are rejected so the
system keeps the safer exact verdict rather than guessing. This is why fuzzy is a
*fallback*, never an override of a confident exact match.

The reference token streams are built automatically by `create_fingerprint.py`
alongside the shingle hashes - no extra step is required.

## Components

| File | Purpose |
|------|---------|
| `fingerprint_core.py` | Shared config, SQLite DB (phonetic tables), phonetic shingle pipeline, scoring |
| `create_fingerprint.py` | Build the phonetic DB from subtitles (`--show`/`--dir`). No transcription. |
| `subtitle_utils.py` | Subtitle parsing + OpenSubtitles download |
| `stt_utils.py` | Speech-to-text helpers (Vosk / Google) - used only during identification |
| `identify_dvd_episodes.py` | **Focused batch tool** for the common real use case: name a folder of DVD-ripped episodes for Plex. Multi-point dialogue sampling + parallel workers, CSV/JSON output with manual-review flags. See **[USAGE_DVD.md](USAGE_DVD.md)**. |
| `selftest.py` | Quick end-to-end phonetic self-test |
| `dvd_identifier_fluent.py` | **Desktop GUI (Fluent / Windows 11)** front end for the DVD identifier - point-and-click, dark theme, sidebar navigation. Built with PySide6-Fluent-Widgets. See [Desktop GUI](#desktop-gui-windows) below. |
| `gui_config.py` | GUI settings store - persists the fingerprint DB path and options to `gui_config.json`. |
| `fluent_launcher.bat` | Double-click launcher for the GUI on Windows (uses `pythonw`, no console window). |

## Development setup

The project is pip-installable. Installing in **editable** mode adds the
project directory to your environment so you can run the tools from anywhere
and hack on the source with changes taking effect immediately.

```bash
# from the project root, ideally in a virtual environment
pip install -e .

# include the dev tools (pytest) as well:
pip install -e ".[dev]"
```

Installing this way keeps the existing flat layout untouched - the scripts and
the Windows launchers (`fluent_launcher.bat`, `python dvd_identifier_fluent.py`)
keep working exactly as before.

### Console entry points

After `pip install -e .` three commands are available on your PATH:

| Command | Runs | Equivalent to |
|---------|------|---------------|
| `dvd-gui` | Fluent desktop GUI | `python dvd_identifier_fluent.py` |
| `dvd-identify` | Batch DVD identifier CLI | `python identify_dvd_episodes.py` |
| `dvd-fingerprint` | Build reference DB from subtitles | `python create_fingerprint.py` |

For example:

```bash
dvd-fingerprint --dir ./subtitles --show-title "Matlock"
dvd-identify --dir ./dvd_rips --db fingerprints.db
dvd-gui
```

### Running the tests

Tests live in `tests/` and run with `pytest`:

```bash
pip install -e ".[dev]"    # installs pytest
pytest                     # runs the suite

# or, without installing the extras:
pip install pytest
python -m pytest -q
```

The included tests are lightweight smoke tests (packaging metadata + core
imports) that run without a Vosk model, ffmpeg, or a populated database. Put
larger test fixtures (sample subtitles, short clips) under `tests/fixtures/`.

## Desktop GUI (Windows)

Prefer not to use the command line? Run the point-and-click app:

```bash
python dvd_identifier_fluent.py
```

...or on Windows just **double-click `fluent_launcher.bat`** (it uses `pythonw`,
so no console window appears).

The GUI is built with **PySide6-Fluent-Widgets** for a native Windows 11 Fluent
look (dark theme, sidebar navigation). It is installed automatically by
`pip install -r requirements.txt`; you also need `ffmpeg` (see
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)).

The window has a sidebar with four sections:

- **Identify** - pick a folder (or single file) of DVD rips, tweak samples /
  sample length / review threshold / parallel workers, then hit **Identify**.
  Results appear in a table with a colour-coded *needs-review* flag. Buttons let
  you **Export CSV/JSON** or **Rename for Plex** (safe: it *copies*
  confidently-identified TV episodes into a `Show/Season NN/Show - SxxEyy.ext`
  layout and never touches files flagged for review).
- **Build Library** - grow the reference DB: add subtitle files (`.srt`/`.vtt`)
  for phonetic matching. It runs `create_fingerprint.py` and streams its output
  live.
- **Settings** - set the `fingerprints.db` path and default options; they are
  persisted to `gui_config.json` (via `gui_config.py`) and reused on the next
  launch.
- **Log** - the full engine log for the last run, for troubleshooting.

Identification and library builds run on a background thread, so the window
stays responsive; a progress bar shows activity. When identifying many files,
the `--workers` setting runs multiple files in parallel for faster batch
processing.

### One-click install (Windows)

Instead of installing dependencies by hand, run the setup script - it uses
`winget` to install everything and creates a Desktop / Start-menu shortcut:

```
Double-click  install.bat        (simplest)
```

or from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

What `install.ps1` does (all idempotent - safe to re-run):

| Step | Installed via | Notes |
|------|---------------|-------|
| Python 3 | `winget install Python.Python.3.12` | skipped if already present |
| FFmpeg | `winget install Gyan.FFmpeg` | decodes video/audio for STT |
| Python packages | `pip install -r requirements.txt` | into a local `.venv` |
| Vosk speech model | download + unzip into `models\` | offline STT for phonetic matching |
| Shortcuts | `WScript.Shell` | Desktop + Start menu, custom icon |

Useful switches: `-NoVenv` (use system Python), `-NoModel` (skip the STT model
download), `-NoShortcut`, and `-Force` (reinstall packages / re-download the
model).

### Build a standalone MSIX installer (Windows)

To produce a self-contained `.msix` that installs the app **without needing
Python on the target machine**:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_msix.ps1 -Version 1.0.0.0
```

This runs PyInstaller to bundle `dvd_identifier_fluent.py` (plus `config.json`
and the Vosk model) into an app folder, packs it into an `.msix` with
`packaging\AppxManifest.xml`, and signs it with a **self-signed** certificate.
`ffmpeg.exe` is bundled automatically if it is on `PATH` at build time (run
`install.ps1` first so it exists).

Build prerequisites (build machine only):

- Python 3 on `PATH` (PyInstaller is installed automatically into `.buildvenv`)
- Windows 10/11 SDK for `makeappx.exe` + `signtool.exe`:
  `winget install Microsoft.WindowsSDK.10.0.22621`

Because the package is self-signed, install it by trusting the generated
certificate once (the script prints the exact commands), then double-click the
`.msix` or run `Add-AppxPackage -Path .\DVDEpisodeIdentifier_1.0.0.0.msix`. A
certificate from a real CA would remove the trust step for end users.

Packaging files live in `packaging\` (`AppxManifest.xml`, `make_icons.py`, and
generated `app.ico` + `assets\`). Regenerate the icons any time with
`python packaging\make_icons.py`.

## Fuzzy matching configuration (`config.json` - `"matching"."fuzzy"`)

The order-preserving phonetic LCS fallback. It runs when the exact phonetic
result is missing or below confidence threshold.

| Key | Meaning |
|-----|---------|
| `enabled` | Turn the fuzzy fallback on/off (default `true`) |
| `min_lcs_ratio` | Minimum `LCS length / query tokens` to accept a fuzzy match (default 0.45) |
| `min_query_tokens` | Skip fuzzy when the query has fewer phonetic tokens than this - too little signal to trust (default 8) |
| `min_margin` | The top candidate must beat the runner-up by this confidence gap, else the match is treated as ambiguous and rejected (default 0.12) |
| `weight` | Scale applied to the raw fuzzy confidence before thresholding (default 1.0) |
