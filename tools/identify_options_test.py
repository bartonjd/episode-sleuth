#!/usr/bin/env python3
"""Offscreen check: the Identify Options card must never be clipped.

Reproduces the reported distortion (a stale/small splitter top-pane size while
the Options card is expanded in Advanced mode) and asserts that every spin box
in the Options grid is fully visible inside the card after the fix.

Run:  QT_QPA_PLATFORM=offscreen python tools/identify_options_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402

OUT_DIR = ROOT / "test_screenshots"


def _fully_visible(widget, ancestor) -> bool:
    """True if widget's rect maps fully inside ancestor's visible area."""
    tl = widget.mapTo(ancestor, widget.rect().topLeft())
    br = widget.mapTo(ancestor, widget.rect().bottomRight())
    return (tl.x() >= -1 and tl.y() >= -1
            and br.x() <= ancestor.width() + 1
            and br.y() <= ancestor.height() + 1)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT_DIR.mkdir(exist_ok=True)
    results: list[str] = []
    ok = True

    win = MainWindow()
    iface = win.identify_interface

    # Force Advanced mode so the Options card is shown, then make sure it is
    # expanded (the reported distortion was an expanded-but-clipped card).
    iface.cfg.set("view_mode", "Advanced")
    iface.apply_view_preferences()
    if iface.opt_card.is_collapsed():
        iface.opt_card.set_collapsed(False)

    win.resize(1440, 900)
    win.stackedWidget.setCurrentWidget(iface)
    win.show()
    for _ in range(4):
        app.processEvents()

    # Simulate a stale saved splitter size that starves the top pane: give the
    # controls far less than they need and let the table hog the rest.
    iface.splitter.setSizes([80, 1200])
    for _ in range(6):
        app.processEvents()

    spins = [iface.samples_spin, iface.samplelen_spin,
             iface.review_spin, iface.workers_spin]
    clipped = [s.__class__.__name__
               for s in spins if not _fully_visible(s, iface.opt_card)]
    card_ok = not clipped and not iface.opt_card.is_collapsed()
    ok &= card_ok
    results.append(
        f"[{'PASS' if card_ok else 'FAIL'}] Options spin boxes visible in card "
        f"(clipped={clipped or 'none'}); "
        f"top pane sizes={iface.splitter.sizes()}")

    # The controls pane must have been forced back up to fit its content.
    top_min = iface.top_widget.minimumHeight()
    hint = iface.top_widget.sizeHint().height()
    min_ok = top_min >= hint - 1
    ok &= min_ok
    results.append(
        f"[{'PASS' if min_ok else 'FAIL'}] top pane min height {top_min} "
        f">= content hint {hint}")

    shot = OUT_DIR / "identify_options_expanded.png"
    win.grab().save(str(shot))
    results.append(f"        screenshot -> {shot.name}")

    print("\n".join(results))
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
