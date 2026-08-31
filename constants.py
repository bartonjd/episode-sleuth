#!/usr/bin/env python3
"""Centralized constants for the audio_fingerprint project.

Single source of truth for default paths and the known Vosk speech models,
so the engine, CLI and GUI all agree on the same values instead of each
hard-coding its own copy.
"""
from __future__ import annotations

# Default file / directory names (relative to the project root).
DEFAULT_DB_PATH = "fingerprints.db"
DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_MODELS_DIR = "models"

# Known Vosk English models keyed by a friendly "size". The small model is the
# default (fast, ~40 MB); the large model is far more accurate on clean audio
# (~1.8 GB) and is what you want to push DVD-rip confidence higher.
VOSK_MODELS = {
    "small": {
        "dir": "vosk-model-small-en-us-0.15",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "approx_mb": 40,
    },
    "large": {
        "dir": "vosk-model-en-us-0.22",
        "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
        "approx_mb": 1800,
    },
}
