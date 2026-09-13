#!/usr/bin/env python3
"""Offscreen verification for the Build library and Settings pages.

Covers the issues reported against the GUI:
  * Building no longer opens a second application window (the old subprocess
    launched "sys.executable -m cli.build_fingerprints", which under a frozen
    build re-launched the whole GUI). We assert the top-level window count does
    not grow when a build starts, and that the in-process LibraryBuildWorker is
    used.
  * The build shows elapsed time and a per-file status line.
  * The Settings page is wrapped in a scroll area so its cards never squish and
    overlap on short windows.
  * The Settings panel stays usable before AND during an ongoing build.

Run:  QT_QPA_PLATFORM=offscreen python tools/build_settings_test.py
Outputs PNGs into test_screenshots/ and prints a pass/fail summary.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.workers import LibraryBuildWorker  # noqa: E402

OUT_DIR = ROOT / "test_screenshots"

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Good evening everyone and welcome to the courtroom.

2
00:00:05,000 --> 00:00:09,000
Your honor, I object to this line of questioning.

3
00:00:10,000 --> 00:00:14,000
Overruled. The witness will answer the question.
"""


def _make_fixture(dirpath: Path) -> None:
    for idx in (1, 2, 3):
        (dirpath / f"Show.S01E0{idx}.srt").write_text(SAMPLE_SRT, encoding="utf-8")


def _visible_windows(app) -> int:
    return sum(1 for w in app.topLevelWidgets() if w.isWindow() and w.isVisible())


def _has_scroll_area(widget) -> bool:
    return widget.findChild(QScrollArea) is not None


def _settle(app, ms: int = 450) -> None:
    """Spin a real event loop so page fade-in animations finish before grab().

    Under the offscreen platform a bare processEvents() captures a blank frame
    mid-animation, so screenshots come out empty; a short real loop lets the
    qfluentwidgets pop/fade animation settle.
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT_DIR.mkdir(exist_ok=True)

    class _R(list):
        def append(self, item):  # noqa: D401
            print(item, flush=True)
            super().append(item)

    results: _R = _R()
    ok = True

    tmp = Path(tempfile.mkdtemp(prefix="es_build_", dir=str(ROOT / ".tmp")
                                if (ROOT / ".tmp").exists() else None))
    (ROOT / ".tmp").mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="es_build_", dir=str(ROOT / ".tmp")))
    subs_dir = tmp / "subs"
    subs_dir.mkdir()
    _make_fixture(subs_dir)
    db_path = str(tmp / "test.db")

    win = MainWindow()
    win.gui_cfg.set("db_path", db_path)
    win.resize(1000, 700)
    win.show()
    app.processEvents()

    build = win.build_interface
    settings = win.settings_interface

    # --- both pages wrapped in a scroll area (anti-squish / distortion fix) ---
    s_ok = _has_scroll_area(settings)
    b_ok = _has_scroll_area(build)
    ok &= s_ok and b_ok
    results.append(f"[{'PASS' if s_ok else 'FAIL'}] Settings page has scroll area")
    results.append(f"[{'PASS' if b_ok else 'FAIL'}] Build page has scroll area")

    # --- short-window distortion check: at 800x600 the settings content is
    # taller than the viewport, i.e. it scrolls instead of squishing cards. ---
    win.resize(800, 600)
    win.stackedWidget.setCurrentWidget(settings)
    for _ in range(4):
        app.processEvents()
    scroll = settings.findChild(QScrollArea)
    content_h = scroll.widget().sizeHint().height()
    viewport_h = scroll.viewport().height()
    dist_ok = content_h > viewport_h  # must overflow -> scrollable, not squished
    ok &= dist_ok
    results.append(
        f"[{'PASS' if dist_ok else 'FAIL'}] Settings scrolls at 800x600 "
        f"(content {content_h}px > viewport {viewport_h}px)")
    _settle(app)
    win.grab().save(str(OUT_DIR / "settings_800x600.png"))
    results.append("        screenshot -> settings_800x600.png")

    win.resize(1000, 760)
    win.stackedWidget.setCurrentWidget(settings)
    app.processEvents()
    _settle(app)
    win.grab().save(str(OUT_DIR / "settings_1000x760.png"))
    results.append("        screenshot -> settings_1000x760.png")

    # --- Settings usable BEFORE a build: switch to it, no unsaved changes ---
    before_leave = settings.confirm_leave()
    ok &= before_leave
    results.append(f"[{'PASS' if before_leave else 'FAIL'}] Settings leaves "
                   f"cleanly before a build (no spurious prompt)")

    # --- start a build and confirm NO second window appears. All "during"
    # checks run immediately after start() with minimal event draining so we
    # observe the build in flight (a 3-file build otherwise finishes instantly).
    win.stackedWidget.setCurrentWidget(build)
    app.processEvents()
    win.build_interface.subs_edit.setText(str(subs_dir))
    windows_before = _visible_windows(app)
    build._build_subs()          # starts the worker; status set synchronously
    app.processEvents()          # single drain - do NOT sleep the build away

    worker_ok = isinstance(build.worker, LibraryBuildWorker)
    ok &= worker_ok
    results.append(f"[{'PASS' if worker_ok else 'FAIL'}] Build uses in-process "
                   f"LibraryBuildWorker (no subprocess)")

    windows_during = _visible_windows(app)
    dual_ok = windows_during <= windows_before
    ok &= dual_ok
    results.append(
        f"[{'PASS' if dual_ok else 'FAIL'}] No second window on build "
        f"(windows before={windows_before}, during={windows_during})")

    # --- elapsed + per-task status shown while building (use isHidden(), which
    # reflects the explicit hidden flag rather than offscreen ancestor state) ---
    status_txt = build.status_label.text().lower()
    status_ok = (not build.status_label.isHidden()
                 and "elapsed" in status_txt)
    ok &= status_ok
    results.append(f"[{'PASS' if status_ok else 'FAIL'}] Elapsed-time status "
                   f"shown during build: {build.status_label.text()!r}")

    win.grab().save(str(OUT_DIR / "build_running.png"))
    results.append("        screenshot -> build_running.png")

    # --- Settings usable DURING an ongoing build (switch tabs, interact) ---
    was_running = build.worker is not None and build.worker.isRunning()
    win.stackedWidget.setCurrentWidget(settings)
    app.processEvents()
    settings.workers_spin.setValue(3)  # interact with a control mid-build
    app.processEvents()
    during_switch_ok = win.stackedWidget.currentWidget() is settings
    ok &= during_switch_ok
    results.append(f"[{'PASS' if during_switch_ok else 'FAIL'}] Settings panel "
                   f"reachable and interactive during a build "
                   f"(worker still running at switch={was_running})")
    win.grab().save(str(OUT_DIR / "settings_during_build.png"))
    results.append("        screenshot -> settings_during_build.png")

    # --- wait for the build to finish and confirm success wiring. Use a real
    # QEventLoop (driven by the worker's done signal, with a safety timeout)
    # rather than a busy processEvents+sleep loop: the latter re-enters and
    # stalls on the non-modal InfoBar animation shown from _on_done under the
    # offscreen platform. A proper event loop handles it exactly like the real
    # app does. ---
    # Return to the Build page to observe completion. Revert the mid-build
    # settings edit first so leaving the (now dirty) Settings tab does not raise
    # an unsaved-changes prompt, and so the build's completion InfoBar has a
    # visible parent (an InfoBar on a hidden page stalls under offscreen).
    settings._revert()
    app.processEvents()
    win.stackedWidget.setCurrentWidget(build)
    app.processEvents()
    leave_ok = win.stackedWidget.currentWidget() is build
    ok &= leave_ok
    results.append(f"[{'PASS' if leave_ok else 'FAIL'}] Returned to Build page "
                   f"after reverting the mid-build Settings edit")

    loop = QEventLoop()
    if build.worker is not None:
        build.worker.done.connect(lambda *_: loop.quit())
    QTimer.singleShot(30000, loop.quit)  # safety net so we never hang forever
    if build.worker is not None and build.worker.isRunning():
        loop.exec()
    app.processEvents()
    finished_ok = build.worker is not None and not build.worker.isRunning()
    done_text_ok = "done" in build.status_label.text().lower()
    ok &= finished_ok and done_text_ok
    results.append(
        f"[{'PASS' if finished_ok and done_text_ok else 'FAIL'}] Build finished "
        f"in-process: status={build.status_label.text()!r}")
    db_made = os.path.exists(db_path)
    ok &= db_made
    results.append(f"[{'PASS' if db_made else 'FAIL'}] Fingerprint DB written "
                   f"to disk ({db_path})")

    # results already streamed to stdout as they were computed (see _R above)
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
