#!/usr/bin/env python3
"""Offscreen verification for the tabbed Settings page.

Confirms the Settings page now uses a horizontal Pivot with five tabs
(Speech-to-Text, Matching, Database, Appearance, About), that switching tabs
swaps the visible stacked page, that the per-tab Reset button relabels itself
(and disables on the About tab which has nothing to reset), that every original
field widget still exists, and that save/snapshot logic is intact.

Run:  QT_QPA_PLATFORM=offscreen python tools/settings_tabs_test.py
Outputs PNGs into test_screenshots/ and prints a pass/fail summary.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _settle(app, ms: int = 400) -> None:
    """Spin the event loop briefly so the Pivot slide animation finishes."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()

from gui.main_window import MainWindow  # noqa: E402

OUT_DIR = ROOT / "test_screenshots"

EXPECTED_TABS = ["stt", "matching", "database", "appearance", "about"]
EXPECTED_TITLES = {
    "stt": "Speech-to-Text",
    "matching": "Matching",
    "database": "Database",
    "appearance": "Appearance",
    "about": "About",
}
# Every widget the rest of settings.py relies on must survive the refactor.
REQUIRED_WIDGETS = [
    "db_edit", "eng_edit", "theme_combo", "density_combo", "mode_combo",
    "workers_spin", "exts_input", "part_switch", "lang_combo", "model_combo",
    "model_download_btn", "model_cancel_btn", "model_status", "model_progress",
    "model_progress_label", "save_btn", "reset_btn",
]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT_DIR.mkdir(exist_ok=True)

    win = MainWindow()
    win.resize(1100, 780)
    st = win.settings_interface
    win.switchTo(st)
    win.show()
    app.processEvents()

    results: list[tuple[str, bool, str]] = []

    # 1. Pivot has exactly the five expected tabs, in order.
    keys = list(st._page_by_key.keys())
    ok = keys == EXPECTED_TABS
    results.append(("five tabs in order", ok, f"got {keys}"))

    # 2. All original field widgets still present.
    missing = [n for n in REQUIRED_WIDGETS if not hasattr(st, n)]
    results.append(("all field widgets preserved", not missing,
                    f"missing {missing}" if missing else "all present"))

    # 3. Switch through each tab: correct page shown + Reset button relabels.
    for key in EXPECTED_TABS:
        st._select_tab(key)
        app.processEvents()
        shown = st.stack.currentWidget() is st._page_by_key[key]
        if key == "about":
            btn_ok = (not st.reset_btn.isEnabled()
                      and st.reset_btn.text() == "Reset")
            detail = f"about reset disabled={not st.reset_btn.isEnabled()}"
        else:
            want = f"Reset {EXPECTED_TITLES[key]}"
            btn_ok = st.reset_btn.isEnabled() and st.reset_btn.text() == want
            detail = f"reset text='{st.reset_btn.text()}'"
        results.append((f"tab '{key}' shows + reset label", shown and btn_ok,
                        detail))
        _settle(app)
        win.grab().save(str(OUT_DIR / f"settings_tab_{key}.png"))

    # 4. Per-tab reset restores baseline (edit a matching field, then reset).
    st._select_tab("matching")
    original_workers = st.workers_spin.value()
    st.workers_spin.setValue(original_workers % 16 + 1)
    changed = st.workers_spin.value() != original_workers
    st._reset_matching()
    app.processEvents()
    restored = st.workers_spin.value() == original_workers
    results.append(("matching reset restores value", changed and restored,
                    f"{original_workers} -> edit -> {st.workers_spin.value()}"))

    # 5. Snapshot + save logic intact (snapshot returns all persisted keys).
    snap = st._snapshot()
    snap_keys = {"db_path", "engine_config_path", "theme", "max_workers",
                 "vosk_model_size", "primary_language", "media_extensions",
                 "ignore_part_format_differences", "view_density", "view_mode"}
    results.append(("snapshot exposes all keys",
                    snap_keys.issubset(snap.keys()),
                    f"keys={sorted(snap.keys())}"))

    print("\n=== Settings tabs test ===")
    all_ok = True
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} -- {detail}")
        all_ok = all_ok and ok
    print(f"\nOVERALL {'PASS' if all_ok else 'FAIL'}")
    print(f"Screenshots written to {OUT_DIR}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
