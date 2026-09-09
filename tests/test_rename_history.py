#!/usr/bin/env python3
"""Tests for the rename-history persistence used by the GUI Undo feature."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui import rename_history as RH


def test_empty_history(tmp_path):
    path = str(tmp_path / "hist.json")
    assert RH.load_history(path) == []
    assert RH.peek_last_batch(path) is None
    assert RH.remove_last_batch(path) is None


def test_record_and_peek(tmp_path):
    path = str(tmp_path / "hist.json")
    ops = [{"src": "/a.mkv", "dest": "/plex/a.mkv"},
           {"src": "/b.mkv", "dest": "/plex/b.mkv"}]
    assert RH.record_batch(ops, destination="/plex", path=path) is True
    last = RH.peek_last_batch(path)
    assert last is not None
    assert last["destination"] == "/plex"
    assert len(last["operations"]) == 2
    assert "timestamp" in last


def test_record_ignores_empty(tmp_path):
    path = str(tmp_path / "hist.json")
    assert RH.record_batch([], path=path) is False
    # operations missing src/dest are dropped
    assert RH.record_batch([{"src": "", "dest": ""}], path=path) is False
    assert RH.load_history(path) == []


def test_remove_last_is_lifo(tmp_path):
    path = str(tmp_path / "hist.json")
    RH.record_batch([{"src": "1", "dest": "d1"}], destination="one", path=path)
    RH.record_batch([{"src": "2", "dest": "d2"}], destination="two", path=path)
    popped = RH.remove_last_batch(path)
    assert popped["destination"] == "two"
    assert RH.peek_last_batch(path)["destination"] == "one"
    remaining = RH.remove_last_batch(path)
    assert remaining["destination"] == "one"
    assert RH.peek_last_batch(path) is None


def test_corrupt_file_is_safe(tmp_path):
    path = str(tmp_path / "hist.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not valid json ]")
    assert RH.load_history(path) == []
    # a fresh record still works over a corrupt file
    assert RH.record_batch([{"src": "x", "dest": "y"}], path=path) is True
