#!/usr/bin/env python3
"""Path presentation helpers.

Qt's file dialogs always return paths with forward slashes, even on Windows,
so a folder like ``D:\\Matlock (1986)\\Season 4`` shows up in the UI as
``D:/Matlock (1986)/Season 4``. That looks foreign to Windows users. These
helpers give a single translation layer so every path shown in the UI uses the
separator native to the running OS, while anything we hand to the engine is
normalised consistently.

Use :func:`native_path` for display (line edits, dialogs, labels) and
:func:`normalise_path` when storing a path in config. The engine itself accepts
either separator (it goes through ``os``/``pathlib``), so this is purely about
presentation and consistency, never about changing which file is referenced.
"""
from __future__ import annotations

import os


def native_path(path: str) -> str:
    """Return ``path`` with separators native to the current OS, for display.

    On Windows this converts forward slashes to backslashes; on Linux/macOS it
    converts backslashes to forward slashes. Empty input is returned unchanged,
    and the value is never otherwise altered (no case-folding, no resolving of
    symlinks or ``..`` segments) so the displayed path still round-trips.
    """
    if not path:
        return path
    text = str(path).strip()
    if not text:
        return text
    # Normalise every separator to the OS-native one without collapsing the
    # path (os.path.normpath would drop a trailing slash and rewrite "." etc.).
    if os.sep == "\\":
        return text.replace("/", "\\")
    return text.replace("\\", "/")


def normalise_path(path: str) -> str:
    """Return a cleaned path suitable for storing in config.

    Uses the OS-native separators (via :func:`native_path`) so saved values are
    consistent with what the user sees. Whitespace around the value is trimmed.
    """
    if not path:
        return path
    return native_path(str(path).strip())
