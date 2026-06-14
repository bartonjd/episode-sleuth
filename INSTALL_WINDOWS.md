# Installing Chromaprint (`fpcalc`) on Windows

The acoustic (sound-based) fingerprinting features of this project need a small
free tool called **`fpcalc.exe`**, which is part of **Chromaprint**. This guide
walks you through installing it, step by step. No programming experience needed.

> **In a hurry?** Just run the automatic installer instead:
> open PowerShell in this folder and run
> `powershell -ExecutionPolicy Bypass -File .\install_chromaprint.ps1`
> (see [Option A](#option-a-automatic-recommended) below). The manual steps are
> here in case you prefer to do it yourself.

---

## What is `fpcalc` and why do I need it?

- `fpcalc.exe` reads audio and produces an **acoustic fingerprint** (a digital
  "signature" of how something *sounds*).
- The Python package `pyacoustid` (already in `requirements.txt`) calls
  `fpcalc.exe` behind the scenes.
- Without `fpcalc.exe`, the phonetic (dialogue) features still work, but the
  acoustic / hybrid features (`--acoustic`, `--both`) will fail with an error
  like *"fpcalc not found"*.

You only need to do this **once**.

---

## Two ways to install

| Option | Best for | Effort |
|--------|----------|--------|
| **A. Automatic** (PowerShell or `.bat`) | Most people | Easiest — one command |
| **B. Manual** | If scripts are blocked, or you like doing it yourself | A few clicks |

---

## Option A: Automatic (recommended)

This project ships with two ready-made installers. Pick whichever you prefer.

### A1. PowerShell installer (most capable)

1. Open the project folder in **File Explorer** (the folder that contains this
   file, `INSTALL_WINDOWS.md`).
2. Click the address bar at the top, type `powershell`, and press **Enter**.
   A blue PowerShell window opens **already in this folder**.
3. Paste this command and press **Enter**:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install_chromaprint.ps1
   ```

4. The script downloads Chromaprint, extracts `fpcalc.exe`, and asks whether you
   want to:
   - **(1)** copy `fpcalc.exe` into this project folder (simplest), or
   - **(2)** add it to your user **PATH** (so every program can find it).

   Type `1` or `2` and press **Enter**.
5. When it finishes it runs `fpcalc -version` to prove it works. Done!

> If you ever see a red *"running scripts is disabled on this system"* message,
> use the exact command above — the `-ExecutionPolicy Bypass` part safely allows
> this one script to run without changing any system settings.

### A2. Batch-file installer (simplest, double-click)

If you just want to **check** whether `fpcalc` is installed and get guided
instructions if it isn't:

1. **Double-click** `install_chromaprint.bat` in File Explorer.
2. Follow the on-screen messages.

> The `.bat` file focuses on detection + clear instructions. For a fully
> automatic download + install, use the PowerShell installer (A1) above.

---

## Option B: Manual installation

### Step 1 — Download Chromaprint

1. Go to the official download page: **<https://acoustid.org/chromaprint>**
   (or the GitHub releases page: **<https://github.com/acoustid/chromaprint/releases>**).
2. Under the latest release, download the **Windows** file. It is named like:

   ```
   chromaprint-fpcalc-1.6.0-windows-x86_64.zip
   ```

   (The version number, e.g. `1.6.0`, may be newer — that's fine.)

   Direct link for the current version:
   <https://github.com/acoustid/chromaprint/releases/download/v1.6.0/chromaprint-fpcalc-1.6.0-windows-x86_64.zip>

### Step 2 — Extract the ZIP

1. In File Explorer, find the downloaded `.zip` file (usually in **Downloads**).
2. **Right-click** it → **Extract All…** → **Extract**.
3. Open the extracted folder. Inside you will find **`fpcalc.exe`**
   (it may be inside a sub-folder named like `chromaprint-fpcalc-1.6.0-windows-x86_64`).

### Step 3 — Put `fpcalc.exe` somewhere the project can find it

You have two choices. **Either one works** — you do **not** need both.

#### Choice 1 (easiest): copy it into the project folder

1. Copy **`fpcalc.exe`**.
2. Paste it directly into this project folder (the one containing
   `acoustic_fingerprint.py` and this file).

That's it. When you run the tools from this folder, they will find `fpcalc.exe`
right next to them.

> Tip: If you put it somewhere else, you can tell the project exactly where it is
> by editing `config.json` and setting the full path, e.g.:
> ```json
> "acoustic": {
>     "fpcalc_path": "C:\\Tools\\chromaprint\\fpcalc.exe"
> }
> ```
> (Use double backslashes `\\` in JSON.)

#### Choice 2 (recommended for repeated use): add it to your PATH

Adding the folder to your **PATH** means *any* program — including Python — can
find `fpcalc.exe` no matter which folder you're in.

1. Decide on a permanent home for it, e.g. create a folder `C:\Tools\chromaprint`
   and move `fpcalc.exe` there.
2. Press the **Windows key**, type **"environment variables"**, and click
   **"Edit the system environment variables"**.
3. In the window that opens, click the **"Environment Variables…"** button
   (bottom-right).
4. In the **top** box ("User variables for *your name*"), click the row named
   **`Path`**, then click **"Edit…"**.
   - If there is no `Path` row, click **"New…"** and create one named `Path`.
5. Click **"New"** and paste the folder path (e.g. `C:\Tools\chromaprint`).
   - **Important:** paste the *folder*, **not** the full path to `fpcalc.exe`.
6. Click **OK** on every window to save.
7. **Close and reopen** any PowerShell / Command Prompt windows so they pick up
   the new PATH.

Visual summary of the PATH dialog:

```
System Properties
  └─ [Environment Variables…]
       └─ User variables  →  Path  →  [Edit…]
            └─ [New]  →  C:\Tools\chromaprint   →  [OK]
```

---

## Verifying the installation

Open a **new** PowerShell or Command Prompt window and run:

```powershell
fpcalc -version
```

You should see something like:

```
fpcalc version 1.6.0
```

If you copied `fpcalc.exe` **into the project folder** (Choice 1) but did **not**
add it to PATH, test it from inside the project folder like this:

```powershell
.\fpcalc.exe -version
```

Then confirm the project itself can use it. From the project folder run a quick
acoustic identify (replace the file name with any audio/video clip you have):

```powershell
python create_acoustic_fingerprint.py --file "some_clip.mp4" --identify
```

If you get past the *"fpcalc not found"* stage, you're all set. 🎉

---

## Troubleshooting

### "`fpcalc` is not recognized as an internal or external command"
- You opened the terminal **before** adding it to PATH. Close the window and
  open a **new** one.
- Or you added the wrong thing to PATH. Make sure you added the **folder**
  (e.g. `C:\Tools\chromaprint`), **not** the file `fpcalc.exe` itself.
- Quick alternative: copy `fpcalc.exe` directly into the project folder
  (Choice 1 above) and run the project tools from there.

### "running scripts is disabled on this system" (PowerShell)
- Run the installer with the bypass flag, which only affects that one run:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\install_chromaprint.ps1
  ```

### Windows SmartScreen / antivirus warns about the download
- `fpcalc.exe` is a well-known open-source tool, but fresh downloads can trigger
  a generic warning. Download only from the official sources:
  - <https://acoustid.org/chromaprint>
  - <https://github.com/acoustid/chromaprint/releases>
- On a SmartScreen prompt, click **"More info" → "Run anyway"** if you trust the
  official source.

### The project still says it can't find `fpcalc`
- Tell it the exact location in `config.json`:
  ```json
  "acoustic": {
      "fpcalc_path": "C:\\Tools\\chromaprint\\fpcalc.exe"
  }
  ```
- Or set an environment variable that `pyacoustid` also understands:
  ```powershell
  setx FPCALC "C:\Tools\chromaprint\fpcalc.exe"
  ```
  (Then open a new terminal.)

### I downloaded the wrong file
- For 64-bit Windows (almost everyone today), use the file ending in
  **`windows-x86_64.zip`**. There is also a 32-bit `windows-i686.zip` for very
  old systems — only use that if your Windows is 32-bit.

### `ffmpeg` errors when fingerprinting video files
- `fpcalc` handles plain audio on its own, but this project also uses **FFmpeg**
  to slice audio out of video. If you plan to fingerprint video files, install
  FFmpeg too: <https://www.gyan.dev/ffmpeg/builds/> (download the "release"
  build, extract, and add its `bin` folder to PATH the same way as above).

---

## Summary checklist

- [ ] Downloaded `chromaprint-fpcalc-…-windows-x86_64.zip`
- [ ] Extracted `fpcalc.exe`
- [ ] Either copied it into the project folder **or** added its folder to PATH
- [ ] Opened a **new** terminal and `fpcalc -version` prints a version number
- [ ] (Optional) Installed FFmpeg if you want to fingerprint **video** files

You're ready to use the acoustic features:

```powershell
python identify_audio.py --both          # live mic, phonetic + acoustic
python fingerprint_audio.py --file clip.mp4 --acoustic --identify
```
