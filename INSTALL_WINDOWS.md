# Installing EpisodeSleuth on Windows

This guide walks you through setting up everything **EpisodeSleuth** (the
phonetic dialogue fingerprinting system) needs on a Windows machine. No
programming experience required.

> **In a hurry?** Just double-click `install.bat` - it runs the automated
> setup script which handles everything below. The manual steps are here in
> case you prefer to do it yourself.

---

## What you need

| Component | Why | Install method |
|-----------|-----|----------------|
| **Python 3.10+** | Runs all the scripts | `winget install Python.Python.3.12` or [python.org](https://www.python.org/downloads/) |
| **FFmpeg** | Decodes video/audio so the STT engine can process it | `winget install Gyan.FFmpeg` or [ffmpeg.org](https://ffmpeg.org/download.html) |
| **Vosk speech model** | Offline speech-to-text engine (phonetic matching) | Downloaded automatically by `install.ps1` |
| **Python packages** | PySide6-Fluent-Widgets, Vosk, metaphone, etc. | `pip install -r requirements.txt` |

---

## Option A: Automatic (recommended)

1. Open the project folder in File Explorer.
2. **Double-click `install.bat`.**
3. Follow any prompts (it may ask to install Python or FFmpeg via `winget`).
4. When it says "Setup complete", you are done. A Desktop shortcut
   ("EpisodeSleuth") will have been created.

From PowerShell you can also run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Useful flags:

| Flag | Effect |
|------|--------|
| `-NoVenv` | Install Python packages into the system Python instead of a `.venv` |
| `-NoModel` | Skip the Vosk model download |
| `-NoShortcut` | Do not create Desktop / Start-menu shortcuts |
| `-Force` | Reinstall packages and re-download the model even if present |

---

## Option B: Manual step-by-step

### 1. Install Python 3

Download from [python.org](https://www.python.org/downloads/) (tick "Add
Python to PATH" during install), or via winget:

```
winget install Python.Python.3.12
```

Verify:

```
python --version
```

### 2. Install FFmpeg

FFmpeg is needed to decode audio from video files so the speech-to-text engine
can process it.

```
winget install Gyan.FFmpeg
```

Or download manually from [ffmpeg.org](https://ffmpeg.org/download.html),
extract, and add the `bin\` folder to your system PATH.

Verify:

```
ffmpeg -version
```

### 3. Install Python packages

From the project folder:

```
pip install -r requirements.txt
```

This installs PySide6-Fluent-Widgets (the GUI toolkit), Vosk (speech-to-text),
Double Metaphone (phonetic encoding), and other dependencies.

### 4. Download the Vosk speech model

The offline speech-to-text engine needs a language model. Download and extract
the small English model:

1. Download: <https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip>
2. Extract the zip so you get a folder `vosk-model-small-en-us-0.15`
3. Move that folder into the project's `models\` directory:
   ```
   models\vosk-model-small-en-us-0.15\
   ```

(`install.ps1` does this automatically.)

---

## Running the app

- **GUI:** double-click the **EpisodeSleuth** shortcut (or `fluent_launcher.bat`),
  or run `python -m audio_fingerprint.gui`
- **CLI:** `python -m audio_fingerprint.cli.identify --dir "C:\path\to\dvd_rips"`

See [README.md](README.md) and [USAGE_DVD.md](USAGE_DVD.md) for full usage.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python` not found | Make sure "Add Python to PATH" was ticked during install, or open a new terminal |
| `ffmpeg` not found | Open a new terminal (PATH refresh), or add FFmpeg's `bin\` folder to PATH manually |
| Vosk model errors | Check that `models\vosk-model-small-en-us-0.15\` exists and contains model files |
| GUI won't start | Run `pip install -r requirements.txt` again to ensure PySide6-Fluent-Widgets is installed |
