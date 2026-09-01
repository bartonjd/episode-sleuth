# EpisodeSleuth - Phonetic Audio Fingerprinting for TV & Movie Identification

**EpisodeSleuth** is a **dialogue-based episode identifier** for DVD rips and TV recordings. Unlike
acoustic fingerprinting systems (which match audio waveforms), this system
matches **what is being said** - the semantic content of the dialogue. It builds
a phonetic reference database from **subtitles** (text), then identifies unknown
audio by transcribing it with speech-to-text and matching the dialogue against
the database.

**Key distinction:** This is a **phonetic/semantic** system, not acoustic. It
matches words and their phonetic representations, not audio waveforms or
spectrograms. If the dialogue is the same but spoken by different actors, in a
different language dub, or with different background music, it will still match
- because the system fingerprints the text, not the sound.

### Workflow at a glance

```
BUILD REFERENCE DATABASE (no transcription needed):
  subtitles (.srt/.vtt)  --> phonetic fingerprints   (python -m audio_fingerprint.cli.build_fingerprints)

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

The reference token streams are built automatically by the reference-DB builder
(`python -m audio_fingerprint.cli.build_fingerprints`) alongside the shingle
hashes - no extra step is required.

## Components

| File | Purpose |
|------|---------|
| `engine/` | **Core matching engine** package - the reusable, UI-agnostic library: media discovery, dialogue sampling/transcription, exact + fuzzy scoring, metadata boosts, batch orchestration, and CSV/JSON writers. Imported by the CLI and the GUI. See [Architecture](#architecture) below. |
| `cli/` | **Thin command-line wrappers** package - argument parsing + I/O only, delegating all real work to `engine/`. `cli/identify.py` (batch identifier) and `cli/build_fingerprints.py` (reference-DB builder). |
| `fingerprint_core.py` | Shared config, SQLite DB (phonetic tables), phonetic shingle pipeline, scoring |
| `subtitle_utils.py` | Subtitle parsing + OpenSubtitles download |
| `stt_utils.py` | Speech-to-text helpers (Vosk / Google) - used only during identification |
| `create_fingerprint.py` | **Deprecated shim** - re-exports `cli/build_fingerprints.py`; emits a `DeprecationWarning`. Kept for backward compatibility. |
| `identify_dvd_episodes.py` | **Deprecated shim** - re-exports the `engine/` API and `cli/identify.py`; emits a `DeprecationWarning`. Kept for backward compatibility. See **[USAGE_DVD.md](USAGE_DVD.md)**. |
| `selftest.py` | Quick end-to-end phonetic self-test |
| `gui/` | **Desktop GUI (Fluent / Windows 11)** package - the point-and-click, dark-theme, sidebar-navigation front end for the DVD identifier. Built with PySide6-Fluent-Widgets; run with `python -m audio_fingerprint.gui`. See [Desktop GUI](#desktop-gui-windows) below. |
| `gui_config.py` | GUI settings store - persists the fingerprint DB path and options to `gui_config.json`. |
| `fluent_launcher.bat` | Double-click launcher for the GUI on Windows (uses `pythonw`, no console window). |

## Architecture

The project is organized into three layers so that the core matching logic is
reusable and independent of any front end:

```
engine/   core matching engine (reusable, UI-agnostic)
  types.py       EpisodeGuess / FileResult dataclasses, media-extension constants
  discovery.py   media discovery, filename parsing + suggested-name building
  scoring.py     fuzzy staging, time weighting, weighted queries, metadata boosts,
                 adaptive review threshold (re-exports score_matches from fingerprint_core)
  matcher.py     audio probing/extraction, sampling, transcription, identify_one()
  batch.py       batch_identify() orchestration (sequential + parallel), CSV/JSON writers

cli/      thin command-line wrappers (argparse + I/O only)
  identify.py            batch DVD identifier - delegates to engine.batch.batch_identify
  build_fingerprints.py  reference-DB builder (formerly create_fingerprint.py)

gui/      desktop Fluent GUI - imports directly from engine/

constants.py        shared defaults (DB / config / models paths, Vosk model catalog)
logging_config.py   setup_logging() used by both the CLI and the GUI entry points
```

- **`engine/`** contains all the real work and has no dependency on the CLI or
  the GUI. Import it directly in your own code:

  ```python
  from engine import identify_one, FileResult, batch_identify, discover_media
  ```

- **`cli/`** wrappers only parse arguments and handle input/output, then call
  into `engine/`. Run them as modules:

  ```bash
  python -m audio_fingerprint.cli.identify --dir /path/to/episodes
  python -m audio_fingerprint.cli.build_fingerprints --show "Matlock" --year 1986
  ```

- **`gui/`** imports the same `engine/` API used by the CLI, so the GUI and the
  command line share one implementation.

**Backward compatibility:** the original top-level scripts remain as thin
re-export shims - `identify_dvd_episodes.py` re-exports the `engine/` API and
`cli/identify.py`'s `main`, and `create_fingerprint.py` re-exports
`cli/build_fingerprints.py`. Both still run and import exactly as before, but
emit a `DeprecationWarning` pointing at the new locations. Update your imports to
`from engine import ...` and your commands to `python -m audio_fingerprint.cli.*`.

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
the Windows launchers (`fluent_launcher.bat`, `python -m audio_fingerprint.gui`)
keep working exactly as before.

### Console entry points

After `pip install -e .` three commands are available on your PATH:

| Command | Runs | Equivalent to |
|---------|------|---------------|
| `dvd-gui` | Fluent desktop GUI | `python -m audio_fingerprint.gui` |
| `dvd-identify` | Batch DVD identifier CLI | `python -m audio_fingerprint.cli.identify` |
| `dvd-fingerprint` | Build reference DB from subtitles | `python -m audio_fingerprint.cli.build_fingerprints` |

For example:

```bash
dvd-fingerprint --dir ./subtitles --show-title "Matlock"
dvd-identify --dir ./dvd_rips --db fingerprints.db
dvd-gui
```

#### Faster builds with parallel workers

Building the reference database parses every subtitle file and phonetically
encodes its dialogue - the slow part for large collections. Pass `--workers` to
process files in parallel (default `4`; the GUI uses the "Max parallel workers"
value from the Settings page):

```bash
# fingerprint a big folder of subtitles using 8 workers
dvd-fingerprint --workers 8 --db my.db --dir /path/to/srt/
```

The parser/encoder runs across a pool of worker processes while database writes
stay serialised on one connection, so results are byte-for-byte identical to a
sequential build. Use `--workers 1` to force the original sequential behaviour.
The parse/encode stage scales close to linearly with cores; overall wall-clock
speedup is bounded by the serial database-write stage (roughly 2x on typical
libraries). If a process pool is unavailable (e.g. a restricted or frozen
environment) the builder automatically falls back to threads, then to
sequential, so a build never fails outright.

If the database path's parent directory does not exist it is created
automatically; an unwritable path reports a clear error instead of a cryptic
SQLite failure.

### Testing

Tests live in `tests/` and run with `pytest`:

```bash
pip install -e ".[test]"       # installs pytest + pytest-mock
pytest                         # run everything
pytest -v                      # verbose
pytest -m "not slow"           # skip the slow integration test
pytest tests/test_matcher.py   # a single file
```

The whole suite is **headless and offline** - no Vosk model, no ffmpeg and no
network are required. Speech-to-text is mocked, and the reference database is
built at test time from the small subtitle fixtures in `tests/fixtures/`, so it
always matches the current schema.

Coverage by module:

| File | What it covers |
| --- | --- |
| `test_matcher.py` | `identify_one` - correct match on matching audio, low-confidence/review on non-matching audio, no-transcriber path (STT mocked) |
| `test_scoring.py` | time-weighted coverage, contiguous-run bonus, +15% show / +10% episode-title boosts, fuzzy fallback on a degraded transcript |
| `test_discovery.py` | `discover_media`, `parse_episode_info` (S01E03 / 1x03 / 103 / S01E01E02), `clean_subtitle_filename`, filename helpers |
| `test_config.py` | `AppConfig` load/merge, enum validation, backward compat, save round-trip |
| `test_subtitle_utils.py` | `.srt` / `.vtt` parsing, Double-Metaphone phonetic encoding, OpenSubtitles helper (network mocked) |
| `test_integration.py` | end-to-end: build fingerprints -> identify -> export CSV/JSON (marked `slow`) |
| `test_build.py` | `validate_db_path` (auto-create / clear errors) and parallel-vs-sequential build equivalence |
| `test_basic.py` | packaging metadata + core-import smoke tests |

Fixtures (in `tests/fixtures/`): `sample.srt`, `sample_ep2.srt`, `sample.vtt`,
`sample_audio.wav` (30 s synthetic clip) and a prebuilt `test_fingerprints.db`.
Network calls (OpenSubtitles) are mocked; the live variant is skipped by
default. Slow tests are marked with `@pytest.mark.slow`.

## Configuration

Settings live in two JSON files at the project root, and a typed layer in
`config.py` unifies and validates them:

| File | Purpose | Managed by |
| --- | --- | --- |
| `config.json` | Engine/algorithm settings (STT, fingerprint, matching, audio, opensubtitles, logging) | `fingerprint_core.load_config()` |
| `gui_config.json` | GUI preferences (theme, workers, last-used paths, model size) | `gui_config.GuiConfig` |

### Typed config layer (`config.py`)

`config.py` exposes three dataclasses:

- `EngineConfig` - STT + database knobs (`stt_engine`, `vosk_model_size`,
  `vosk_model_path`, `google_language`, `db_path`) plus the full `config.json`
  payload preserved in `raw`.
- `GuiConfig` - GUI preferences (theme, `max_workers`, last paths, etc.).
- `AppConfig` - the root object combining `engine` + `gui` + shared `db_path`.

```python
from config import AppConfig

app = AppConfig.load()          # reads config.json + gui_config.json, merges, validates
print(app.db_path, app.engine.vosk_model_size, app.gui.theme)
app.engine.vosk_model_size = "large"
app.save()                      # writes both files back
```

Shared keys (`db_path`, `vosk_model_size`) can appear in both files; the GUI
value wins because it reflects the user's most recent choice in the app.

Example `config.json` (engine settings, abbreviated):

```json
{
  "fingerprint": { "shingle_sizes": [3, 4, 5], "hash_algorithm": "md5" },
  "database": { "path": "fingerprints.db" },
  "matching": { "confidence_threshold": 0.15, "fuzzy": { "enabled": true } },
  "stt": {
    "engine": "vosk",
    "vosk_model_path": "models/vosk-model-small-en-us-0.15",
    "model_size": "small"
  }
}
```

### Validation

`AppConfig.validate()` checks values and coerces invalid enums to safe
defaults (non-fatal, logged as warnings):

- `theme` must be one of `Dark`, `Light`, `Auto` (else `Dark`).
- `vosk_model_size` must be `small` or `large` (else `small`).
- `max_workers` must be an integer >= 1 (else 4).
- Non-empty `engine_config_path` / `vosk_model_path` are reported if missing.

### Backward compatibility and migration

- Existing `config.json` and `gui_config.json` files load unchanged - the file
  format is untouched, so nothing needs migrating by hand.
- `load_config()` still returns the same `config.json`-shaped dict every engine
  caller already expects; internally it now delegates to
  `load_typed_config()` (which returns an `AppConfig`).
- `GuiConfig` keeps its full dict-like interface. Passing
  `GuiConfig(migrate_to_unified=True)` routes its storage through `AppConfig`
  while still reading/writing `gui_config.json`.
- `AppConfig.load(make_backups=True)` writes one-time `.bak` copies of the
  source files before the first unified save.

## Desktop GUI (Windows)

Prefer not to use the command line? Run the point-and-click app:

```bash
python -m audio_fingerprint.gui
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
  for phonetic matching. It runs `python -m audio_fingerprint.cli.build_fingerprints`
  and streams its output live.
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

### Build a standalone executable (Windows or Linux)

Want a runnable app with **no Python required** on the target machine? Build a
self-contained executable with PyInstaller - the cleanest launch method (no
`fluent_launcher.bat`, just double-click the produced binary):

```powershell
# Windows -> dist\EpisodeSleuth\EpisodeSleuth.exe
powershell -ExecutionPolicy Bypass -File .\build_binary.ps1
```

```bash
# Linux / macOS -> dist/EpisodeSleuth/EpisodeSleuth
./build_binary.sh
```

Both wrappers build inside a throwaway `.buildvenv` and drive one shared,
cross-platform spec (`episodesleuth.spec`). Switches: `-BundleModel` /
`BUNDLE_MODEL=1` packs the Vosk model for a fully offline app; `-OneFile` /
`ONEFILE=1` produces a single file instead of a folder. A normal one-folder
build is roughly ~400 MB (mostly Qt + Vosk); `ffmpeg` is still needed at run
time and is not bundled. Full details in
**[BUILD_EXECUTABLES.md](BUILD_EXECUTABLES.md)**.

### Build a standalone MSIX installer (Windows)

To produce a self-contained `.msix` that installs the app **without needing
Python on the target machine**:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_msix.ps1 -Version 1.0.0.0
```

This runs PyInstaller to bundle the `audio_fingerprint.gui` package (plus `config.json`
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
`.msix` or run `Add-AppxPackage -Path .\EpisodeSleuth_1.0.0.0.msix`. A
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

## Contributing

Contributions are welcome - bug reports, feature ideas, and pull requests.

**How changes get merged (who can push):**

- The **`main` branch is protected**. Only the repository owner (**bartonjd**)
  and any collaborators the owner explicitly adds on GitHub can push directly to
  it. This is enforced by GitHub repository permissions, *not* by the software
  license - an open-source license controls what you may do with the code, but
  it never grants anyone write access to this specific repository.
- **Everyone else contributes by forking and opening a pull request:**

  ```bash
  # 1. Fork the repo on GitHub, then clone your fork
  git clone https://github.com/<your-user>/episode-sleuth.git
  cd episode-sleuth

  # 2. Create a feature branch
  git checkout -b my-improvement

  # 3. Make changes, run the tests, then commit and push to YOUR fork
  pytest
  git commit -am "Describe your change"
  git push origin my-improvement

  # 4. Open a Pull Request against bartonjd/episode-sleuth -> main
  ```

- The owner reviews each pull request and merges it when it is ready. This keeps
  full control of what lands in the official project with the owner, while still
  letting anyone use, modify, and propose changes to the code.

By submitting a contribution you agree that it is licensed under the project's
Apache License 2.0 (see below), per section 5 of that license.

## License

EpisodeSleuth is licensed under the **Apache License, Version 2.0**. See the
[LICENSE](LICENSE) and [NOTICE](NOTICE) files for the full text.

Apache-2.0 was chosen (over MIT) because it gives the project a bit more
protection while remaining permissive and business-friendly:

- **You may** use, modify, distribute, and build commercial products on top of
  EpisodeSleuth, for free, without asking permission.
- **You must** keep the copyright and license notices, state any changes you
  made to the files, and include a copy of the license with any redistribution.
- It adds an **explicit patent grant** (contributors can't later sue users over
  patents covering their contributions) and a **trademark clause** (the license
  doesn't hand out rights to the "EpisodeSleuth" name/branding) - protections
  MIT does not spell out.

Note that the *license* and *repository push access* are two separate things:
the Apache-2.0 license governs everyone's rights to the code, while who can push
to this repo's `main` branch is controlled by GitHub permissions (see
[Contributing](#contributing) above).

Third-party components (Vosk, PySide6, PySide6-Fluent-Widgets, FFmpeg, etc.)
remain under their own licenses; see [NOTICE](NOTICE) for details.
