#!/usr/bin/env python3
"""Render the Identify results table at 1500 and 1920 wide to verify the grid
fills the full viewport width (Item A: _fill_table_width).

Run: PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tools/grid_fill_shots.py
Writes PNGs into test_screenshots/ and prints the measured right-edge gap.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from engine.types import EpisodeGuess, FileResult  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "test_screenshots"

SAMPLE = [
    ("Title_t00.mkv", "S01E01", "Diagnosis of Murder",
     "correct", "", 0.93, 5, 5),
    ("Title_t01.mkv", "S01E02", "The Court-Martial of Sam Flagg",
     "rename", "Matlock (1986) - S01E02 - The Court-Martial of Sam Flagg.mkv",
     0.78, 4, 5),
    ("Title_t02.mkv", "S01E03", "The Judge (a very long episode title "
     "that will need to be truncated with an ellipsis in narrow columns)",
     "rename", "Matlock (1986) - S01E03 - The Judge.mkv", 0.55, 3, 5),
    ("Title_t03.mkv", "S01E04", "The People vs. Matlock",
     "review", "Matlock (1986) - S01E04 - The People vs. Matlock.mkv",
     0.32, 2, 5),
]


def make_results():
    out = []
    for fn, eid, etitle, status, sugg, conf, votes, total in SAMPLE:
        g = EpisodeGuess(episode_id=eid, title="Matlock (1986)",
                         season=1, episode=int(eid[-2:]), votes=votes,
                         total_samples=total, mean_confidence=conf,
                         method="phonetic", episode_title=etitle)
        out.append(FileResult(
            filename=fn, path=str(ROOT / fn), duration_s=1300.0, guess=g,
            needs_review=(status == "review"), notes="",
            name_status=status, suggested_filename=sugg))
    return out


def measure_and_shot(win, iface, app, width: int) -> int:
    win.resize(width, 840)
    for _ in range(6):
        app.processEvents()
    iface._update_auto_hidden()
    iface._fill_table_width()
    app.processEvents()
    table = iface.table
    avail = table.viewport().width()
    used = sum(table.columnWidth(c) for c in range(table.columnCount())
               if not table.isColumnHidden(c))
    gap = avail - used
    out = OUT / f"grid_fill_{width}.png"
    win.grab().save(str(out))
    print(f"width={width}: viewport={avail} used={used} gap={gap} -> {out}")
    return gap


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.mkdir(exist_ok=True)
    win = MainWindow()
    win.show()
    app.processEvents()
    iface = win.identify_interface
    iface.results = make_results()
    iface._rebuild_table()
    win.stackedWidget.setCurrentWidget(iface)

    g1 = measure_and_shot(win, iface, app, 1500)
    g2 = measure_and_shot(win, iface, app, 1920)

    ok = abs(g1) <= 3 and abs(g2) <= 3
    print("OVERALL", "PASS" if ok else "FAIL",
          "(gap within 3px of full width at both sizes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
