# Installing EpisodeSleuth on Linux

This guide covers installing **EpisodeSleuth** (the phonetic dialogue
fingerprinting system) on a 64-bit Linux machine. No programming experience
required.

There are two ways to install, from easiest to most involved:

| Option | Best for | Needs Python? |
|--------|----------|:-------------:|
| **A. Standalone package (recommended)** | Just running the app | No |
| **B. Source install** (`pip`) | Developers, or running from source | Yes |

> The only external tool the standalone app still needs at run time is
> **FFmpeg** (for decoding audio):
> ```
> sudo apt install ffmpeg      # Debian / Ubuntu / Mint
> sudo dnf install ffmpeg      # Fedora
> sudo pacman -S ffmpeg        # Arch
> ```

---

## Option A: Standalone package (recommended, no Python)

This is the simplest path. You get a self-contained app - no Python, no `pip`,
no virtual environment.

You can install from either the `.tar.gz` (works on any distro) or, on
Debian/Ubuntu, the `.deb`.

### A1: From the .tar.gz (any distro)

1. Get **`EpisodeSleuth-<version>-linux-x64.tar.gz`** (from the project's
   Releases, or build it yourself with `./package_linux.sh` - see
   [BUILD_EXECUTABLES.md](BUILD_EXECUTABLES.md)).
2. Extract it:
   ```bash
   tar -xzf EpisodeSleuth-<version>-linux-x64.tar.gz
   cd EpisodeSleuth-<version>-linux-x64
   ```
3. Install:
   ```bash
   ./install.sh          # installs for the current user (~/.local)
   sudo ./install.sh     # installs system-wide (/opt, on PATH for everyone)
   ```
   Or skip installing and just run it in place:
   ```bash
   ./EpisodeSleuth/EpisodeSleuth
   ```
4. After installing, launch **EpisodeSleuth** from your desktop application
   menu, or run `episodesleuth` in a terminal.

To uninstall (match the scope you installed with):

```bash
./uninstall.sh          # or: sudo ./uninstall.sh
```

> If `~/.local/bin` is not on your `PATH`, the installer prints a note. Add it
> to your shell profile, or launch from the app menu instead.

### A2: From the .deb (Debian / Ubuntu)

```bash
sudo apt install ./episodesleuth_<version>_amd64.deb
```

This installs into `/opt/episodesleuth`, adds an `episodesleuth` command on
`PATH`, and creates an application-menu entry. `ffmpeg` is pulled in
automatically as a dependency. Remove it with:

```bash
sudo apt remove episodesleuth
```

---

## Option B: Source install (developers)

Use this if you want to run from source with your own Python, or you are
developing EpisodeSleuth.

### 1. Install prerequisites

```bash
sudo apt install python3 python3-venv python3-pip ffmpeg   # Debian/Ubuntu
```

### 2. Create a virtual environment and install packages

From the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This installs PySide6-Fluent-Widgets (the GUI toolkit), Vosk (speech-to-text),
Double Metaphone (phonetic encoding), and other dependencies.

### 3. Download the Vosk speech model

The offline speech-to-text engine needs a language model. Either download it
from the app's **Settings** page on first run, or fetch it manually:

1. Download: <https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip>
2. Extract so you get a folder `vosk-model-small-en-us-0.15`
3. Move it into the project's `models/` directory:
   ```
   models/vosk-model-small-en-us-0.15/
   ```

---

## Running the app

- **Standalone (Option A):** launch **EpisodeSleuth** from your app menu, or run
  `episodesleuth` in a terminal.
- **Source install (Option B) - GUI:** `python -m audio_fingerprint.gui`
- **Source install (Option B) - CLI:**
  `python -m audio_fingerprint.cli.identify --dir "/path/to/dvd_rips"`

See [README.md](README.md) and [USAGE_DVD.md](USAGE_DVD.md) for full usage.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ffmpeg` not found | Install it: `sudo apt install ffmpeg` (or your distro's package manager) |
| `episodesleuth` command not found | Add `~/.local/bin` to your `PATH`, or install system-wide with `sudo ./install.sh` |
| Vosk model errors | Open Settings and download the model, or check the model folder exists |
| "speech-to-text module could not be loaded" | Standalone: re-download the package (older builds missed the STT deps); Source: `pip install -r requirements.txt` again |
| App menu entry missing | Log out/in, or run `update-desktop-database ~/.local/share/applications` |
| GUI won't start (source install) | Re-run `pip install -r requirements.txt` to ensure PySide6-Fluent-Widgets is installed |
