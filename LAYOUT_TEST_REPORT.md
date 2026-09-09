# EpisodeSleuth - Responsive Layout Test Report

**Date:** 2026-09-09
**Scope:** Main-window layout improvements (splitter, adaptive button bar,
window-geometry persistence, minimum window size) verified across the three
requested target resolutions plus a minimum-size stress case.

## How these results were produced

The GUI is a PySide6 / QFluentWidgets desktop application, so it was exercised
headlessly with Qt's `offscreen` platform plugin configured with a 2560x1440
virtual screen (so windows are not clamped below the target resolutions). For
each resolution the real `MainWindow` is created, shown, resized, and the
window is captured to a PNG with `QWidget.grab()`. These are genuine rendered
frames of the actual widgets, not mockups. They are **not** an interactive
click-through session; runtime behaviour that depends on a live identify run
(populated results, enabled export buttons) was simulated by forcing the
buttons visible for the worst-case wrap test.

Reproduce with:

```
QT_QPA_PLATFORM="offscreen:configfile=tools/offscreen.json" python tools/layout_test.py
```

(`tools/offscreen.json` defines a 2560x1440 virtual screen so windows are not
clamped; the script writes PNGs to `test_screenshots/` and prints a PASS/FAIL
summary).

## Automated checks (all PASS)

| Check | Result |
|-------|--------|
| Minimum window size is 800x600 | PASS |
| Identify page splitter present, vertical orientation, 2 panes | PASS |
| Splitter drag (`setSizes`) yields valid non-zero pane sizes | PASS |
| Window geometry save -> restore round-trip (1500x950 preserved) | PASS |
| Action buttons never clipped at 1366x768 | PASS |
| Action buttons never clipped at 1440x900 | PASS |
| Action buttons never clipped at 1920x1080 | PASS |
| Action buttons wrap to 2 rows (not clipped) at minimum 800x600 | PASS |

Full application test suite: **80 passed, 1 skipped** (`pytest -q`).
Lint: `ruff check .` -> **All checks passed!**

## Per-resolution results

### 1366 x 768
- All controls accessible: header, "DVD rips to identify" card with
  Folder/File pickers, the action button bar, filter box, status tabs
  (All / Rename / Correct / Review), and the results table with every column.
- Action bar fits on a single row (visible buttons width ~1285 px, no wrap
  needed).
- Vertical splitter grip visible between the controls block and the results
  table; dragging redistributes space between the two panes.
- No hidden or clipped content.
- Screenshot: `test_screenshots/identify_1366x768.png`

### 1440 x 900
- Same controls, more vertical room for the results table.
- Action bar on a single row.
- Splitter functional.
- No hidden or clipped content.
- Screenshot: `test_screenshots/identify_1440x900.png`

### 1920 x 1080
- Full-HD layout; controls comfortably spaced, results table gets the
  majority of the vertical space.
- Action bar on a single row.
- Splitter functional.
- No hidden or clipped content.
- Screenshot: `test_screenshots/identify_1920x1080.png`

### 800 x 600 (minimum window size, stress case)
- Window can actually reach the 800x600 minimum (the old horizontal button
  box previously imposed a ~1185 px minimum width; the new FlowLayout removes
  that constraint).
- With every action button forced visible, the bar wraps onto 2 rows
  (row 1: Identify, Cancel, Preview Renames, Rename for Plex, Undo Last
  Rename; row 2: Export JSON, Export CSV, Customize View). Nothing is clipped.
- Filter, status tabs, and the results table remain accessible; the table
  shows a horizontal scrollbar for columns beyond the width, as expected.
- Screenshot: `test_screenshots/identify_800x600_narrow.png`

## Summary

All requested resolutions render correctly with no clipped or hidden
controls. The vertical splitter lets the user trade space between the
options/controls block and the results table, the action button bar adapts by
wrapping instead of overflowing on narrow windows, the window honours an
800x600 minimum, and window size/position persist across sessions via
`gui_config.json` (`window_geometry`) using `QMainWindow.saveGeometry()` /
`restoreGeometry()`. The identify splitter position is likewise persisted
(`identify_splitter`).
