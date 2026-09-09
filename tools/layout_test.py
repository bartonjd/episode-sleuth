#!/usr/bin/env python3
"""Offscreen layout verification for EpisodeSleuth.

Renders the main window at several common resolutions, captures a PNG of each,
and checks the responsive-layout guarantees (minimum window size, vertical
splitter present and draggable, action buttons wrapping instead of clipping,
window-geometry save/restore round-trip).

Run:  QT_QPA_PLATFORM=offscreen python tools/layout_test.py
Outputs PNGs into test_screenshots/ and prints a pass/fail summary.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402

RESOLUTIONS = [(1366, 768), (1440, 900), (1920, 1080)]
OUT_DIR = ROOT / "test_screenshots"


def _btn_within(btn, container) -> bool:
    """True if btn's rectangle sits fully inside container's visible area."""
    top_left = btn.mapTo(container, btn.rect().topLeft())
    bottom_right = btn.mapTo(container, btn.rect().bottomRight())
    return (
        top_left.x() >= 0
        and top_left.y() >= 0
        and bottom_right.x() <= container.width() + 1
        and bottom_right.y() <= container.height() + 1
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT_DIR.mkdir(exist_ok=True)
    results: list[str] = []
    ok = True

    win = MainWindow()
    win.show()
    app.processEvents()
    iface = win.identify_interface

    # --- minimum window size ---
    min_sz = win.minimumSize()
    min_ok = min_sz.width() == 800 and min_sz.height() == 600
    ok &= min_ok
    results.append(f"[{'PASS' if min_ok else 'FAIL'}] minimum window size = "
                   f"{min_sz.width()}x{min_sz.height()} (expected 800x600)")

    # --- splitter present, vertical, 2 panes ---
    sp = iface.splitter
    sp_ok = (sp is not None and sp.orientation() == Qt.Vertical
             and sp.count() == 2)
    ok &= sp_ok
    results.append(f"[{'PASS' if sp_ok else 'FAIL'}] identify splitter: "
                   f"vertical={sp.orientation() == Qt.Vertical}, "
                   f"panes={sp.count()}")

    for w, h in RESOLUTIONS:
        win.resize(w, h)
        win.stackedWidget.setCurrentWidget(iface)
        app.processEvents()
        app.processEvents()

        # every action button must sit inside the actions container
        clipped = [b.objectName() or b.text()
                   for b in iface._action_buttons
                   if not _btn_within(b, iface.actions_widget)]
        # actions widget must be inside the page too
        aw = iface.actions_widget
        rows = 1
        ys = sorted({b.mapTo(aw, b.rect().topLeft()).y()
                     for b in iface._action_buttons})
        rows = len(ys)
        res_ok = not clipped
        ok &= res_ok
        results.append(
            f"[{'PASS' if res_ok else 'FAIL'}] {w}x{h}: "
            f"actions_widget={aw.width()}x{aw.height()} across {rows} row(s), "
            f"clipped_buttons={clipped or 'none'}")

        shot = OUT_DIR / f"identify_{w}x{h}.png"
        win.grab().save(str(shot))
        results.append(f"        screenshot -> {shot.name}")

    # --- splitter drag (setSizes) persists sensible values ---
    sp.setSizes([300, 500])
    app.processEvents()
    sizes = sp.sizes()
    drag_ok = len(sizes) == 2 and all(s > 0 for s in sizes)
    ok &= drag_ok
    results.append(f"[{'PASS' if drag_ok else 'FAIL'}] splitter drag -> "
                   f"sizes={sizes}")

    # --- geometry save / restore round-trip ---
    win.resize(1500, 950)
    win.move(40, 30)
    app.processEvents()
    win._save_geometry()
    win2 = MainWindow()
    restored = win2._restore_geometry()
    win2.show()
    app.processEvents()
    geo_ok = (restored and win2.width() == 1500 and win2.height() == 950)
    ok &= geo_ok
    results.append(f"[{'PASS' if geo_ok else 'FAIL'}] geometry round-trip: "
                   f"restored={restored}, "
                   f"size={win2.width()}x{win2.height()} (expected 1500x950)")

    # --- narrow window: buttons must wrap, not clip, and window can reach min ---
    # Force every action button visible so we exercise the worst case (some are
    # hidden until results exist / depending on the panel preferences).
    for b in iface._action_buttons:
        b.setVisible(True)
    win.resize(800, 600)
    win.stackedWidget.setCurrentWidget(iface)
    for _ in range(5):
        app.processEvents()
    narrow_clip = [b.text() for b in iface._action_buttons
                   if not _btn_within(b, iface.actions_widget)]
    ys = sorted({b.mapTo(iface.actions_widget,
                         b.rect().topLeft()).y()
                 for b in iface._action_buttons})
    narrow_ok = not narrow_clip and len(ys) > 1
    ok &= narrow_ok
    results.append(f"[{'PASS' if narrow_ok else 'FAIL'}] narrow 800x600: "
                   f"button rows={len(ys)}, clipped={narrow_clip or 'none'}")
    win.grab().save(str(OUT_DIR / "identify_800x600_narrow.png"))
    results.append("        screenshot -> identify_800x600_narrow.png")

    print("\n".join(results))
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
