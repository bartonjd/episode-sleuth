# Installing Chromaprint (`fpcalc`) on Windows

The acoustic (sound-based) fingerprinting features of this project need a small\
free tool called **`fpcalc.exe`**, which is part of **Chromaprint**. This guide\
walks you through installing it, step by step. No programming experience needed.

> **In a hurry?** Just run the automatic installer instead:\
> open PowerShell in this folder and run\
> `powershell -ExecutionPolicy Bypass -File .\install_chromaprint.ps1`\
> (see [Option A](#option-a-automatic-recommended) below). The manual steps are\
> here in case you prefer to do it yourself.

---

## What is `fpcalc` and why do I need it?

* `fpcalc.exe` reads audio and produces an **acoustic fingerprint** (a digital\
  "signature" of how something _sounds_).

* The Python package `pyacoustid` (already in `requirements.txt`) calls\
  `fpcalc.exe` behind the scenes.

* Without `fpcalc.exe`, the phonetic (dialogue) features still work, but the\
  acoustic / hybrid features (`--acoustic`, `--both`) will f