#!/usr/bin/env python3
"""audio_fingerprint.cli - command-line wrappers around the engine.

These modules are thin: they parse arguments, wire up the shared engine
(``audio_fingerprint.engine``) and format console output. All the real work
lives in the engine package.

    identify.py          batch DVD identifier   (entry point: dvd-identify)
    build_fingerprints.py  reference DB builder  (entry point: dvd-fingerprint)
"""
from __future__ import annotations

import os
import sys

# The flat engine modules (fingerprint_core, subtitle_utils, stt_utils) live in
# the project root - the parent of this directory. Put it on sys.path so the
# CLI resolves them whether it is run as a module, a script or an entry point.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
