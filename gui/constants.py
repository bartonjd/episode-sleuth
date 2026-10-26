#!/usr/bin/env python3
"""Shared constants for the EpisodeSleuth GUI package.

Kept in one small module so both the pages and the workers can import them
without creating an import cycle with main_window.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QColor

# Project root (where config.json and fingerprints.db live). This file is at
# <root>/gui/constants.py, so two dirnames up.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reuse the project-level filename constants so the GUI, engine and CLI agree.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from constants import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH  # noqa: E402

APP_TITLE = "EpisodeSleuth"
DEFAULT_DB = os.path.join(HERE, DEFAULT_DB_PATH)
DEFAULT_CONFIG = os.path.join(HERE, DEFAULT_CONFIG_PATH)

# Row tint colours (kept subtle so they read well on the dark theme).
COLOR_OK = QColor(45, 125, 60, 70)        # green  - confident
COLOR_MEDIUM = QColor(200, 150, 20, 70)   # amber  - usable but check
COLOR_REVIEW = QColor(200, 55, 55, 80)    # red    - needs review

# Legend swatch colours - opaque versions of the row tints above, used in the
# status legend so the key reads clearly against the page background.
LEGEND_OK = "#2d7d3c"       # green  - Correct
LEGEND_MEDIUM = "#c89614"   # amber  - Rename
LEGEND_REVIEW = "#c83737"   # red    - Review

# Confidence pill colours (opaque, keyed to the match-confidence value rather
# than the naming status). Distinct purpose from the row tints above.
PILL_HIGH = "#2e7d32"       # green  - confident   (>= 0.70)
PILL_MED = "#b8860b"        # amber  - moderate     (>= 0.40)
PILL_LOW = "#c73737"        # red    - weak         (< 0.40)

# Folder scan depth choices. The UI offers three options; each maps to the
# integer depth understood by engine.discovery.discover_media:
#   0  = this folder only (no recursion)
#   1  = one level down (the folder and its immediate subfolders)
#  -1  = all subfolders (unlimited recursion)
SCAN_DEPTH_LABELS = ["This folder only", "One level down", "All subfolders"]
SCAN_DEPTH_VALUES = [0, 1, -1]


def scan_depth_to_index(depth) -> int:
    """Map a stored depth integer to its combo-box index (default: one level)."""
    try:
        return SCAN_DEPTH_VALUES.index(int(depth))
    except (TypeError, ValueError):
        return SCAN_DEPTH_VALUES.index(1)


def index_to_scan_depth(index: int) -> int:
    """Map a combo-box index back to its depth integer (default: one level)."""
    if 0 <= index < len(SCAN_DEPTH_VALUES):
        return SCAN_DEPTH_VALUES[index]
    return 1
