#!/usr/bin/env python3
"""Offscreen screenshots for the settings-persistence / blank-spinbox fix.

Seeds a temporary per-user config (via EPISODESLEUTH_CONFIG_DIR) with
NON-default values so the rendered pages prove the values were loaded from the
file, then renders:

  * the Identify page with the Options card expanded (scale from QT_SCALE_FACTOR)
  * the Settings page Matching tab (scale 1.0 only)
  * the Settings page Database tab, which shows the config-file location
    (scale 1.0 only)

Run:
  QT_QPA_PLATFORM=offscreen PYTHONPATH=. python tools/fix13_shots.py
  QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 PYTHONPATH=. python tools/fix13_shots.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Seed a temp per-user config dir with non-default values BEFORE any GUI import
# so MainWindow loads exactly these from the file.
SEED = {
    "max_workers": 6,
    "samples_per_file": 7,
    "sample_length": 15.0,
    "review_confidence": 0.40,
    "vosk_model_size": "large",
    "scan_depth": -1,
    "primary_language": "English",
}
_cfg_dir = Path(tempfile.mkdtemp(prefix="episodesleuth_shots_"))
os.environ["EPISODESLEUTH_CONFIG_DIR"] = str(_cfg_dir)
with open(_cfg_dir / "gui_config.json", "w", encoding="utf-8") as _fh:
    json.dump(SEED, _fh, indent=2)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "test_screenshots"


def _settle(app, ms: int = 600) -> None:
    """Spin the event loop so fade/slide animations finish before grabbing."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    scale = os.environ.get("QT_SCALE_FACTOR", "1.0")
    tag = scale.replace(".", "_")
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.mkdir(exist_ok=True)

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    app.processEvents()

    # --- Identify page, Options card expanded ---
    idf = win.identify_interface
    win.switchTo(idf)
    if hasattr(idf, "opt_card"):
        idf.opt_card.set_collapsed(False)
    _settle(app)
    out_idf = OUT / f"fix13_identify_options_{tag}.png"
    win.grab().save(str(out_idf))
    print(f"identify options (scale {scale}) -> {out_idf}")
    print("  samples=%s len=%s review=%s workers=%s" % (
        idf.samples_spin.lineEdit().text(),
        idf.samplelen_spin.lineEdit().text(),
        idf.review_spin.lineEdit().text(),
        idf.workers_spin.lineEdit().text()))

    if scale == "1.0":
        st = win.settings_interface
        win.switchTo(st)
        st._select_tab("matching")
        _settle(app)
        out_match = OUT / "fix13_settings_matching.png"
        win.grab().save(str(out_match))
        print(f"settings matching -> {out_match}")
        print("  workers=%s" % st.workers_spin.lineEdit().text())

        st._select_tab("database")
        _settle(app)
        out_db = OUT / "fix13_settings_config_location.png"
        win.grab().save(str(out_db))
        print(f"settings database/config-location -> {out_db}")
        print("  config path label=%s" % st.config_path_label.text())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
