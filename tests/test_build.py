#!/usr/bin/env python3
"""Tests for the reference-library builder: DB path validation and the
parallel / sequential fingerprint-building paths.

These stay headless and offline - they only read the local .srt fixtures and
write to temporary SQLite databases.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fingerprint_core import validate_db_path, FingerprintDB
from cli.build_fingerprints import run_directory


# ---------------------------------------------------------------------------
# Part 2: DB path validation
# ---------------------------------------------------------------------------

def test_validate_db_path_creates_missing_parent(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "fp.db"
    assert not nested.parent.exists()
    validate_db_path(str(nested))
    assert nested.parent.is_dir()


def test_validate_db_path_accepts_memory_and_bare_name():
    # Should not raise and should not try to create anything.
    validate_db_path(":memory:")
    validate_db_path("fingerprints.db")


def test_validate_db_path_clear_error_on_uncreatable_dir():
    # /proc/<x> cannot be created - expect a clear ValueError, not a raw OSError.
    with pytest.raises(ValueError) as exc:
        validate_db_path("/proc/nonexistent_xyz/deeper/fp.db")
    assert "Cannot create database directory" in str(exc.value)


def test_fingerprintdb_autocreates_parent(tmp_path):
    db_path = tmp_path / "fresh" / "dir" / "auto.db"
    db = FingerprintDB(str(db_path))
    try:
        assert db_path.exists()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Part 1: parallel vs sequential build produce identical results
# ---------------------------------------------------------------------------

def _subs_folder(tmp_path, sample_srt, n=4):
    """Create a folder of ``n`` subtitle copies with distinct episode numbers."""
    import shutil
    folder = tmp_path / "subs"
    folder.mkdir(parents=True)
    for i in range(1, n + 1):
        shutil.copy(sample_srt, folder / f"Matlock.S03E{i:02d}.srt")
    return str(folder)


def _build(tmp_path, subs, fp_cfg, workers):
    db_path = tmp_path / f"out_w{workers}.db"
    db = FingerprintDB(str(db_path))
    try:
        grand, processed, skipped = run_directory(
            subs, db, fp_cfg, "Matlock", 1986, "tv",
            force=True, show_title="Matlock", workers=workers)
        stats = db.stats()
    finally:
        db.close()
    return grand, processed, stats


def test_parallel_matches_sequential(tmp_path, sample_srt, fp_cfg):
    subs = _subs_folder(tmp_path / "seq", sample_srt, n=4)
    seq = _build(tmp_path / "seq", subs, fp_cfg, workers=1)

    subs2 = _subs_folder(tmp_path / "par", sample_srt, n=4)
    par = _build(tmp_path / "par", subs2, fp_cfg, workers=4)

    # Same fingerprint count, same processed count, same media rows.
    assert seq[0] == par[0]
    assert seq[1] == par[1] == 4
    assert seq[2].get("media") == par[2].get("media") == 4


def test_workers_one_is_sequential(tmp_path, sample_srt, fp_cfg):
    subs = _subs_folder(tmp_path, sample_srt, n=3)
    grand, processed, stats = _build(tmp_path, subs, fp_cfg, workers=1)
    assert processed == 3
    assert stats.get("media") == 3
    assert grand > 0
