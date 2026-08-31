#!/usr/bin/env python3
"""Shared constants for the DVD Episode Identifier GUI package.

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
from constants import DEFAULT_DB_PATH, DEFAULT_CONFIG_PATH  # noqa: E402

APP_TITLE = "DVD Episode Identifier"
DEFAULT_DB = os.path.join(HERE, DEFAULT_DB_PATH)
DEFAULT_CONFIG = os.path.join(HERE, DEFAULT_CONFIG_PATH)

# Row tint colours (kept subtle so they read well on the dark theme).
COLOR_OK = QColor(45, 125, 60, 70)        # green  - confident
COLOR_MEDIUM = QColor(200, 150, 20, 70)   # amber  - usable but check
COLOR_REVIEW = QColor(200, 55, 55, 80)    # red    - needs review
