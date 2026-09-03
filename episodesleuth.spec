# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for EpisodeSleuth (cross-platform).

Produces a self-contained one-folder application bundle that runs the Fluent
GUI without requiring Python to be installed on the target machine:

    dist/EpisodeSleuth/EpisodeSleuth        (Linux/macOS)
    dist/EpisodeSleuth/EpisodeSleuth.exe    (Windows)

Build it with either of the wrapper scripts (recommended):

    Windows : powershell -ExecutionPolicy Bypass -File .\build_binary.ps1
    Linux   : ./build_binary.sh

or directly:

    pyinstaller episodesleuth.spec --noconfirm

Environment switches (read at build time):

    BUNDLE_MODEL=1   also pack the Vosk speech model from models/ into the
                     bundle, producing a fully offline app (much larger).
                     Default: the model is NOT bundled and is downloaded on
                     first run / by the installer.
    ONEFILE=1        build a single-file executable instead of a one-folder
                     bundle (slower startup; handy for quick sharing).
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory.
PROJECT_ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (SPECPATH is a PyInstaller global)
PARENT = os.path.dirname(PROJECT_ROOT)

BUNDLE_MODEL = os.environ.get("BUNDLE_MODEL", "0") == "1"
ONEFILE = os.environ.get("ONEFILE", "0") == "1"

# --- data files -------------------------------------------------------------
datas = []
_config = os.path.join(PROJECT_ROOT, "config.json")
if os.path.exists(_config):
    datas.append((_config, "."))
# Ship the icon so the running app can set its window / taskbar icon.
_icon_asset = os.path.join(PROJECT_ROOT, "packaging", "app.ico")
if os.path.exists(_icon_asset):
    datas.append((_icon_asset, "packaging"))

binaries = []
hiddenimports = []

# Fully collect the heavy third-party packages (data files + submodules).
# pydub is included here so its whole package (and metadata) is bundled.
for pkg in ("vosk", "qfluentwidgets", "pydub"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # If a package is missing at build time, let Analysis surface it later.
        pass

# The C ``audioop`` module was removed from the stdlib in Python 3.13 (PEP 594)
# and is now shipped by the 'audioop-lts' wheel as a compiled extension named
# ``audioop`` (plus a pure-Python ``pyaudioop`` fallback). pydub imports it at
# module load time, so a frozen build MUST include it or transcription breaks
# with "No module named 'audioop'". PyInstaller does not detect it via pydub's
# lazy import, so pull in its module + dynamic library explicitly.
for _audioop_mod in ("audioop", "pyaudioop"):
    hiddenimports.append(_audioop_mod)
# audioop-lts ships ``audioop`` as a SINGLE top-level extension module (not a
# package), so collect_dynamic_libs may skip it. Locate the compiled file
# directly via importlib and add it as a binary so it is always bundled. This
# runs on the build machine's interpreter (Python 3.13 for end users), where
# audioop resolves to the audioop-lts .pyd/.so. On Python < 3.13 audioop is a
# stdlib extension that PyInstaller already bundles, so this is a harmless no-op.
try:
    import importlib.util as _ilu
    _spec = _ilu.find_spec("audioop")
    if _spec and _spec.origin and _spec.origin not in ("built-in", "frozen"):
        if _spec.origin.endswith((".pyd", ".so", ".dylib")):
            binaries.append((_spec.origin, "."))
except Exception:
    pass

# Make sure our own (flat-layout) modules and their submodules are pulled in.
hiddenimports += collect_submodules("metaphone")
# The GUI ships as a flat top-level ``gui`` package (imported by relative name),
# so collect it directly. ``audio_fingerprint`` is only a resolvable package name
# when the project is pip-installed; collecting it here is a harmless no-op for
# the flat/source layout but keeps installed usage working too.
hiddenimports += collect_submodules("gui")
hiddenimports += collect_submodules("audio_fingerprint")
hiddenimports += collect_submodules("engine")

# Top-level project modules that the GUI imports (stt_utils, subtitle_utils, etc.)
# and their heavy dependencies (pydub, speech_recognition) must be explicit.
hiddenimports += [
    "stt_utils",
    "subtitle_utils",
    "fingerprint_core",
    "config",
    "constants",
    "logging_config",
    "gui_config",
]
# STT stack: pydub (audio manipulation) and speech_recognition (wrapper).
hiddenimports += collect_submodules("pydub")
hiddenimports += collect_submodules("speech_recognition")

# Optionally bundle the offline Vosk model for a no-download experience.
if BUNDLE_MODEL:
    _models = os.path.join(PROJECT_ROOT, "models")
    if os.path.isdir(_models):
        datas.append((_models, "models"))

# Icon: only meaningful on Windows (.ico); ignored on Linux, harmless to pass.
_icon = _icon_asset if os.path.exists(_icon_asset) else None

block_cipher = None

# Heavy scientific / ML / cloud packages that may live in the build environment
# but are NOT used by EpisodeSleuth. Excluding them keeps the bundle small
# (~1.7 GB down to a few hundred MB). The app only needs PySide6,
# qfluentwidgets, vosk and metaphone (numpy is pulled in transitively by
# qfluentwidgets; no external edit-distance library is used).
EXCLUDES = [
    "torch", "torchaudio", "torchvision",
    "tensorflow", "keras",
    "pyarrow",
    "cv2", "opencv_python",
    "scipy",
    "pandas",
    "matplotlib",
    "sklearn", "scikit_learn",
    "sympy",
    "boto3", "botocore", "s3transfer",
    "tables",
    "imageio", "imageio_ffmpeg",
    "IPython", "notebook", "jupyter", "jupyter_client", "ipykernel",
    "PyQt5", "PyQt6", "PySide2",  # avoid mixing Qt bindings
    "tkinter",
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, "gui", "__main__.py")],
    # Both the project root (bare imports: gui_config, engine, fingerprint_core)
    # and its parent (the audio_fingerprint package) must be importable.
    pathex=[PROJECT_ROOT, PARENT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="EpisodeSleuth",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=_icon,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="EpisodeSleuth",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=_icon,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="EpisodeSleuth",
    )
