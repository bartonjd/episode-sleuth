#!/usr/bin/env python3
"""GUI package for the DVD Episode Identifier.

A modern, Windows 11 "Fluent Design" desktop front end built with PySide6 and
PySide6-Fluent-Widgets. This package was split out of the former monolithic
dvd_identifier_fluent.py module; the layout is now:

    gui/
      constants.py      shared constants (paths, colours, titles)
      logging_bridge.py route engine logging records to a Qt signal
      workers.py        IdentifyWorker / BuildWorker / ModelDownloadWorker
      widgets.py        small helper widgets (Card, _path_row)
      main_window.py    MainWindow + main() entry point
      pages/            Identify / Build / Settings / Log interface pages

Run it with:
    python -m audio_fingerprint.gui
or via the ``dvd-gui`` console entry point.
"""
from __future__ import annotations

import os
import sys

# The engine modules (identify_dvd_episodes, fingerprint_core, ...) live at the
# project root and are imported with bare names. Make sure that root is on
# sys.path no matter what CWD the app was launched from.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from audio_fingerprint.gui.main_window import main

__all__ = ["main"]
