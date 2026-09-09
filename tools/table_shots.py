#!/usr/bin/env python3
"""Render demonstration screenshots of the results table (Item 4) with sample
rows at a wide and a narrow width, plus the Preview Renames dialog (Item 5).

Run: QT_QPA_PLATFORM="offscreen:configfile=tools/offscreen.json" \
        python tools/table_shots.py
Writes PNGs into test_screenshots/.
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
from gui.pages.identify import RenamePreviewDialog  # noqa: E402

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

    # Wide: all columns visible.
    win.resize(1500, 820)
    for _ in range(5):
        app.processEvents()
    iface._update_auto_hidden()
    app.processEvents()
    win.grab().save(str(OUT / "table_wide_1500.png"))

    # 1366x768 target.
    win.resize(1366, 768)
    for _ in range(5):
        app.processEvents()
    iface._update_auto_hidden()
    app.processEvents()
    win.grab().save(str(OUT / "table_1366x768.png"))

    # Narrow: least-important columns auto-hidden.
    win.resize(860, 640)
    for _ in range(5):
        app.processEvents()
    iface._update_auto_hidden()
    app.processEvents()
    win.grab().save(str(OUT / "table_narrow_860.png"))

    # Preview Renames dialog with a long list (scrolls; buttons fixed).
    plan = [{"filename": r.filename,
             "dest": str(ROOT / "Matlock" / "Season 01" /
                         (r.suggested_filename or r.filename))}
            for r in iface.results for _ in range(15)]
    dlg = RenamePreviewDialog(plan, str(ROOT / "Matlock"), parent=win)
    dlg.resize(720, 560)
    dlg.show()
    for _ in range(5):
        app.processEvents()
    dlg.grab().save(str(OUT / "dialog_preview_renames.png"))
    dlg.close()

    print("wrote:",
          "table_wide_1500.png, table_1366x768.png, "
          "table_narrow_860.png, dialog_preview_renames.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
