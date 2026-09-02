# Installing EpisodeSleuth on Windows

This guide covers installing **EpisodeSleuth** (the phonetic dialogue
fingerprinting system) on a Windows machine. No programming experience
required.

There are two ways to install, from easiest to most involved:

| Option | Best for | Needs Python? |
|--------|----------|:-------------:|
| **A. Standalone package (recommended)** | Just running the app | No |
| **B. Source install** (`install.bat`) | Developers, or running from source | Yes |

> The only external tool the standalone app still needs at run time is
> **FFmpeg** (for decoding audio). Installing it is one command - see below.

---

## Option A: Standalone package (recommended, no Python)

This is the simplest path. You get a self-contained app - no Python, no `pip`,
no virtual environment.

1. Get **`EpisodeSleuth-<version>-windows-x64.zip`** (from the project's
   Releases, or build it yourself with `package_windows.ps1` - see
   [BUILD_EXECUTABLES.md](BUILD_EXECUTABLES.md)).
2. **Right-click the .zip -> Extract All...** to a folder of your choice.
3. Open the extracted folder. You now have two choices:
   - **Just run it:** open the `EpisodeSleuth` folder and double-click
     **`EpisodeSleuth.exe`**. That's it.
   - **Install with shortcuts:** double-click **`Install-EpisodeSleuth.bat`**.
     Run it normally to install for the current user, or right-click ->
     **Run as administrator** to install for all users. It copies the app into
     Program Files (or your user folder) and creates Desktop + Start-menu
     shortcuts.
4. Install **FFmpeg** (needed to decode audio):
   ```
   winget install Gyan.FFmpeg
   ```
5. On first launch, open the app's **Settings** page and download the offline
   speech model (unless your package already bundles it).

> **SmartScreen warning?** Because the app is not code-signed, Windows may show
> "Windows protected your PC". Click **More info -> Run anyway**. This is
> expected for unsigned apps and is safe for a build you trust. See
> [BUILD_EXECUTABLES.md](BUILD_EXECUTABLES.md#code-signing-and-smartscreen) for
> notes on signing.

To uninstall a shortcut install:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-EpisodeSleuth.ps1 -Uninstall
```

---

## Option B: Source install (developers)

Use this if you want to run from source with your own Python, or you are
developing EpisodeSleuth.

### What you need

| Component | Why | Install method |
|-----------|-----|----------------|
| **Python 3.10+** | Runs all the scripts | `winget install Python.Python.3.12` or [python.org](https://www.python.org/downloads/) |
| **FFmpeg** | Decodes video/audio so the STT engine can process it | `winget install Gyan.FFmpeg` or [ffmpeg.org](https://ffmpeg.org/download.html) |
| **Vosk speech model** | Offline speech-to-text engine (phonetic matching) | Downloaded automatically by `install.ps1` |
| **Python packages** | PySide6-Fluent-Widgets, Vosk, metaphone, etc. | `pip install -r requirements.txt` |

### B1: Automatic

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

### B2: Manual step-by-step

#### 1. Install Python 3

Download from [python.org](https://www.python.org/downloads/) (tick "Add
Python to PATH" during install), or via winget:

```
winget install Python.Python.3.12
```

Verify:

```
python --version
```

#### 2. Install FFmpeg

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

#### 3. Install Python packages

From the project folder:

```
pip install -r requirements.txt
```

This installs PySide6-Fluent-Widgets (the GUI toolkit), Vosk (speech-to-text),
Double Metaphone (phonetic encoding), and other dependencies.

#### 4. Download the Vosk speech model

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

- **Standalone (Option A):** double-click the **EpisodeSleuth** shortcut, or
  `EpisodeSleuth.exe` inside the app folder.
- **Source install (Option B) - GUI:** double-click the **EpisodeSleuth**
  shortcut, or run `python -m audio_fingerprint.gui`
- **Source install (Option B) - CLI:**
  `python -m audio_fingerprint.cli.identify --dir "C:\path\to\dvd_rips"`

See [README.md](README.md) and [USAGE_DVD.md](USAGE_DVD.md) for full usage.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| SmartScreen blocks the app | Click "More info" -> "Run anyway" (unsigned build) |
| `ffmpeg` not found | Run `winget install Gyan.FFmpeg`, then open a new terminal (PATH refresh) |
| Vosk model errors | Open Settings and download the model, or check `models\vosk-model-small-en-us-0.15\` exists |
| "speech-to-text module could not be loaded" | Standalone: re-download the package (older builds missed the STT deps); Source: run `pip install -r requirements.txt` again |
| `python` not found (source install) | Make sure "Add Python to PATH" was ticked during install, or open a new terminal |
| GUI won't start (source install) | Run `pip install -r requirements.txt` again to ensure PySide6-Fluent-Widgets is installed |
