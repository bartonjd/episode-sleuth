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

# Known Vosk models per language (ISO 639-1 code -> {size -> spec}). English
# reuses VOSK_MODELS above so existing behaviour is unchanged. Other languages
# let a non-English library transcribe in its own language instead of forcing
# English STT. Sizes mirror the English "small"/"large" convention; where a
# project publishes only one general model it is listed under both keys.
VOSK_MODELS_BY_LANG = {
    "en": VOSK_MODELS,
    "es": {
        "small": {
            "dir": "vosk-model-small-es-0.42",
            "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
            "approx_mb": 39,
        },
        "large": {
            "dir": "vosk-model-es-0.42",
            "url": "https://alphacephei.com/vosk/models/vosk-model-es-0.42.zip",
            "approx_mb": 1400,
        },
    },
    "fr": {
        "small": {
            "dir": "vosk-model-small-fr-0.22",
            "url": "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip",
            "approx_mb": 41,
        },
        "large": {
            "dir": "vosk-model-fr-0.22",
            "url": "https://alphacephei.com/vosk/models/vosk-model-fr-0.22.zip",
            "approx_mb": 1400,
        },
    },
    "de": {
        "small": {
            "dir": "vosk-model-small-de-0.15",
            "url": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
            "approx_mb": 45,
        },
        "large": {
            "dir": "vosk-model-de-0.21",
            "url": "https://alphacephei.com/vosk/models/vosk-model-de-0.21.zip",
            "approx_mb": 1900,
        },
    },
}
