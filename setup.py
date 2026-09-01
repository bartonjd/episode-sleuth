#!/usr/bin/env python3
"""Setup script for EpisodeSleuth (phonetic DVD episode identifier).

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
    "create_fingerprint",      # deprecated shim -> cli.build_fingerprints
    "identify_dvd_episodes",   # deprecated shim -> engine + cli.identify
    "stt_utils",
    "gui_config",
    "selftest",
]

# The core engine (``audio_fingerprint.engine``), the CLI wrappers
# (``audio_fingerprint.cli``) and the desktop GUI (``audio_fingerprint.gui``)
# are all shipped as proper sub-packages so their imports resolve both in-place
# and when installed. package_dir maps the top-level ``audio_fingerprint``
# package to the project root, which is where this file (and __init__.py) live.
SUB_PACKAGES = [
    "audio_fingerprint",
    "audio_fingerprint.engine",
    "audio_fingerprint.cli",
    "audio_fingerprint.gui",
    "audio_fingerprint.gui.pages",
]

setup(
    name="episode-sleuth",
    version="1.0.0",
    description="EpisodeSleuth - phonetic 'Shazam for dialogue' that identifies "
                "TV/movie episodes from DVD-rip audio by matching transcribed "
                "speech against a subtitle-built reference database.",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="EpisodeSleuth contributors",
    license="Apache-2.0",
    python_requires=">=3.9",
    py_modules=PY_MODULES,
    packages=SUB_PACKAGES,
    package_dir={"audio_fingerprint": "."},
    install_requires=_read_requirements(),
    extras_require={
        "test": ["pytest>=7.0", "pytest-mock>=3.10"],
        "dev": ["pytest>=7.0", "pytest-mock>=3.10"],
    },
    entry_points={
        "console_scripts": [
            # EpisodeSleuth entry points
            "episodesleuth = audio_fingerprint.gui:main",
            "episodesleuth-identify = audio_fingerprint.cli.identify:main",
            "episodesleuth-fingerprint = audio_fingerprint.cli.build_fingerprints:main",
            # Backwards-compatible aliases (kept so existing docs/scripts work)
            "dvd-gui = audio_fingerprint.gui:main",
            "dvd-identify = audio_fingerprint.cli.identify:main",
            "dvd-fingerprint = audio_fingerprint.cli.build_fingerprints:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Topic :: Multimedia :: Sound/Audio :: Analysis",
        "Intended Audience :: End Users/Desktop",
    ],
)
