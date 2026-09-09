#!/usr/bin/env python3
"""Persistent log of Plex rename/copy batches, used to power "Undo Last Rename".

Each identify-and-rename run copies files into a Plex-style layout. So the user
can reverse a mistake, every batch is appended to a small JSON history file in
the user's home directory (``~/.episode_sleuth/rename_history.json``). The file
is a list of batch records, oldest first:

    [
      {
        "timestamp": "2026-09-09T12:34:56",
        "destination": "/media/plex",
        "operations": [
          {"src": "/rips/a.mkv", "dest": "/media/plex/Show/Season 01/..."},
          ...
        ]
      },
      ...
    ]

All reads and writes are deliberately defensive: a missing or corrupt file
simply behaves like an empty history so the GUI never crashes over it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

# Keep the whole history bounded so the file cannot grow without limit.
MAX_BATCHES = 50


def history_dir() -> str:
    """Directory that holds the rename history file (created on demand)."""
    return os.path.join(os.path.expanduser("~"), ".episode_sleuth")


def history_path() -> str:
    """Absolute path to the rename history JSON file."""
    return os.path.join(history_dir(), "rename_history.json")


def load_history(path: Optional[str] = None) -> List[Dict]:
    """Return the list of batch records (oldest first). Never raises."""
    path = path or history_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return []


def _save_history(batches: List[Dict], path: Optional[str] = None) -> bool:
    """Write the whole history list back to disk. Returns success."""
    path = path or history_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(batches[-MAX_BATCHES:], fh, indent=2)
        return True
    except OSError:
        return False


def record_batch(operations: List[Dict[str, str]], destination: str = "",
                 path: Optional[str] = None) -> bool:
    """Append a completed rename batch to the history.

    ``operations`` is a list of ``{"src": ..., "dest": ...}`` mappings for the
    files that were successfully copied. An empty list is ignored (nothing to
    undo). Returns True when the batch was recorded.
    """
    ops = [{"src": o["src"], "dest": o["dest"]}
           for o in operations if o.get("src") and o.get("dest")]
    if not ops:
        return False
    batches = load_history(path)
    batches.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "destination": destination,
        "operations": ops,
    })
    return _save_history(batches, path)


def peek_last_batch(path: Optional[str] = None) -> Optional[Dict]:
    """Return the most recent batch record without removing it, or None."""
    batches = load_history(path)
    return batches[-1] if batches else None


def remove_last_batch(path: Optional[str] = None) -> Optional[Dict]:
    """Pop and return the most recent batch record, persisting the change."""
    batches = load_history(path)
    if not batches:
        return None
    last = batches.pop()
    _save_history(batches, path)
    return last
