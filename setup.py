#!/usr/bin/env python3
"""Setup script for the audio-fingerprint (phonetic DVD identifier) project.

This keeps the current flat layout (all modules live in the project root) so
that the existing scripts and Windows launchers keep working exactly as before,
while also making the project pip-installable with convenient console entry
points. File locations are intentionally NOT reorganized here.
"""
import os

from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))

# --- long description (from README) -----------------------------------------
try:
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
        LONG_DESCRIPTION = fh.read()
except OSError:
    LONG_DESCRIPTION = ""


# --- dependencies (parsed from requirements.txt) ----------------------------
def _read_requirements():
    """Return install_requires parsed from requirements.txt.

    Skips blank lines and comments, and strips trailing inline comments so the
    two files never drift out of sync.
    """
    reqs = []
    path = os.path.join(HERE, "requirements.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # strip inline comments (e.g. "vosk>=0.3.45  # offline STT")
                line = line.split("#", 1)[0].strip()
                if line:
                    reqs.append(line)
    except OSError:
        pass
    return reqs


# Top-level modules that make up the (currently flat) package. Listed
# explicitly so setuptools does not try to auto-discover packages.
PY_MODULES = [
    "fingerprint_core",
    "subtitle_utils",
    "create_fingerprint",
    "identify_dvd_episodes",
    "stt_utils",
    "gui_config",
    "selftest",
]

# The GUI now lives in the ``audio_fingerprint.gui`` sub-package. It is shipped
# as a proper package (rather than a flat module) so that its absolute imports
# resolve both when run in-place and when installed. package_dir maps the
# top-level ``audio_fingerprint`` package to the project root, which is where
# this file (and __init__.py) live.
GUI_PACKAGES = [
    "audio_fingerprint",
    "audio_fingerprint.gui",
    "audio_fingerprint.gui.pages",
]

setup(
    name="audio-fingerprint",
    version="1.0.0",
    description="Phonetic 'Shazam for dialogue' - identify TV/movie episodes "
                "from DVD-rip audio by matching transcribed speech against a "
                "subtitle-built reference database.",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="audio-fingerprint contributors",
    license="MIT",
    python_requires=">=3.9",
    py_modules=PY_MODULES,
    packages=GUI_PACKAGES,
    package_dir={"audio_fingerprint": "."},
    install_requires=_read_requirements(),
    extras_require={
        "dev": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "dvd-gui = audio_fingerprint.gui:main",
            "dvd-identify = identify_dvd_episodes:main",
            "dvd-fingerprint = create_fingerprint:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Multimedia :: Sound/Audio :: Analysis",
        "Intended Audience :: End Users/Desktop",
    ],
)
