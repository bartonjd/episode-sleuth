#!/usr/bin/env python3
"""Entry point for ``python -m gui`` (and the frozen EpisodeSleuth executable).

This module is run as the top-level ``__main__`` script - both by
``python gui/__main__.py`` and as the PyInstaller entry point - so it cannot
rely on being imported inside a package. It bootstraps ``sys.path`` with the
project root (the directory that contains the ``gui`` package and the flat
engine modules) and then imports the GUI package by name. This works no matter
what the containing folder is called (``episode-sleuth``, ``episode-sleuth-main``,
etc.), which the old ``from audio_fingerprint.gui...`` import did not.
"""
from __future__ import annotations

import os
import sys

# Project root = parent of the directory that holds this file (i.e. the folder
# that contains the ``gui`` package and the top-level engine modules).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gui.main_window import main

if __name__ == "__main__":
    main()
