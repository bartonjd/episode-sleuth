"""End-to-end integration test.

Builds a reference DB from the subtitle fixtures, identifies a clip (with
transcription mocked so no ffmpeg / Vosk is needed), and exports the results to
CSV and JSON - the full happy-path workflow the CLI and GUI drive.
"""
import csv
import json
import os
from types import SimpleNamespace

import pytest

import engine.matcher as matcher
from engine.batch import batch_identify, write_csv, write_json
from fingerprint_core import FingerprintConfig, FingerprintDB, load_config
import cli.build_fingerprints as cf


pytestmark = pytest.mark.slow


def _args():
    return SimpleNamespace(
        points=[0.1, 0.3, 0.5, 0.7, 0.9],
        sample_len=15.0,
        review_confidence=0.40,
        runtime_tolerance=4.0,
        show_title="Matlock",
    )


def test_end_to_end_workflow(tmp_path, monkeypatch, sample_srt, sample_ep2_srt,
                             sample_audio, ep1_noisy_transcript):
    # ---- 1. build the reference library from subtitles --------------------
    cfg = load_config()
    fp_cfg = FingerprintConfig.from_config(cfg)

    ep1 = tmp_path / "Matlock (1986) - S01E01 - The Trial.srt"
    ep2 = tmp_path / "Matlock (1986) - S01E02 - The Harbor.srt"
    ep1.write_text((open(sample_srt).read()))
    ep2.write_text((open(sample_ep2_srt).read()))

    db_path = str(tmp_path / "ref.db")
    db = FingerprintDB(db_path)
    n1 = cf.fingerprint_subtitle_file(str(ep1), db, fp_cfg, "Matlock", 1986,
                                      show_title="Matlock")
    n2 = cf.fingerprint_subtitle_file(str(ep2), db, fp_cfg, "Matlock", 1986,
                                      show_title="Matlock")
    assert n1 > 0 and n2 > 0
    assert db.stats()["media"] == 2
    db.close()

    # ---- 2. identify the clip (transcription mocked) ----------------------
    duration = 1400.0
    per_window = [(duration * f, ep1_noisy_transcript)
                  for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    monkeypatch.setattr(matcher, "_probe_duration", lambda path: duration)
    monkeypatch.setattr(matcher, "transcribe_samples",
                        lambda *a, **k: (per_window, len(per_window)))

    results = batch_identify([sample_audio], db_path, fp_cfg, cfg, _args(),
                             transcriber=object(), workers=1)
    assert len(results) == 1
    r = results[0]
    assert r.guess is not None
    assert r.guess.episode_id == "S01E01"

    # ---- 3. export CSV + JSON ---------------------------------------------
    csv_path = str(tmp_path / "episode_map.csv")
    json_path = str(tmp_path / "episode_map.json")
    write_csv(results, csv_path)
    write_json(results, json_path)

    assert os.path.exists(csv_path) and os.path.exists(json_path)

    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["episode_id"] == "S01E01"

    with open(json_path) as fh:
        data = json.load(fh)
    assert data[0]["episode_id"] == "S01E01"
    assert data[0]["title"] == "Matlock"
