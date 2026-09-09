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



---

# Results Table and Dialog Improvements (Items 4 & 5)

**Date:** 2026-09-09

## Navigation icon fix

"Build library" and "Manage library" previously used two book-style icons
(`FIF.LIBRARY` and `FIF.BOOK_SHELF`) that render almost identically. "Build
library" now uses `FIF.ADD` (a plus icon, signalling "create"), while "Manage
library" keeps `FIF.BOOK_SHELF`. The two entries are now visually distinct
(see `test_screenshots/table_wide_1500.png` - the left navigation shows a
magnifier, a plus, and a book).

## How these results were produced

Same method as above: real widgets exercised under Qt's `offscreen` platform
with a 2560x1440 virtual screen, captured with `QWidget.grab()`. Automated
assertions live in `tools/table_dialog_test.py`; demonstration screenshots are
produced by `tools/table_shots.py`. These are rendered frames, not an
interactive click session; the header right-click menu and drag-to-reorder are
verified programmatically (menu construction, `moveSection`, persistence
round-trip) rather than by simulating mouse gestures.

Reproduce with:

```
QT_QPA_PLATFORM="offscreen:configfile=tools/offscreen.json" python tools/table_dialog_test.py
QT_QPA_PLATFORM="offscreen:configfile=tools/offscreen.json" python tools/table_shots.py
```

## Item 4 - results table (30 automated checks, all PASS)

| Area | Result |
|------|--------|
| Long text elides with "..." (`ElideRight`, word-wrap off) | PASS |
| Every text cell has a tooltip carrying its full value | PASS |
| Horizontal + vertical scrolling is per-pixel (smooth) | PASS |
| Column headers are drag-to-reorder (`setSectionsMovable`) | PASS |
| Header right-click menu has a checkable entry per column | PASS |
| Column priority defined (Status > Episode > Match% > File > Title > Suggested > Agree) | PASS |
| Auto-hide: nothing hidden >= 1000px; more hidden as width shrinks | PASS |
| Essential columns (Select, Status, Episode, Match%, Notes) never auto-hidden or user-hidden | PASS |
| Widening restores auto-hidden columns | PASS |
| User show/hide choice persists (`column_hidden`) | PASS |
| Column order persists and restores in a new session (`column_order`) | PASS |

Auto-hide thresholds (table viewport width): `< 1000px` hides Samples Agree;
`< 900px` also hides Suggested Name; `< 780px` also hides Episode Title;
`< 640px` also hides File. Essentials always remain, and a horizontal
scrollbar covers anything that still does not fit.

Screenshots:
- `test_screenshots/table_wide_1500.png` - all columns visible at 1500px.
- `test_screenshots/table_1366x768.png` - 1366x768 target, all columns fit,
  long Episode Title / Suggested Name cells show the ellipsis.
- `test_screenshots/table_narrow_860.png` - at 860px, Episode Title, Suggested
  Name and Samples Agree are auto-hidden; Status text elides ("Rena...").

## Item 5 - dialogs (Preview Renames, Customize View)

| Check | Result |
|-------|--------|
| Preview dialog height capped at 80% of screen | PASS |
| Preview dialog file list scrolls; button row fixed at bottom | PASS |
| Preview dialog buttons wrap via FlowLayout on narrow windows | PASS |
| Preview dialog min width clamped to fit small screens | PASS |
| Customize dialog content wrapped in a `QScrollArea` | PASS |
| Customize dialog height capped at 80%; buttons in FlowLayout | PASS |
| Customize dialog still reports checkbox selections correctly | PASS |

Screenshot: `test_screenshots/dialog_preview_renames.png` - a 60-item preview
list scrolling with the Cancel / Proceed with copy buttons fixed at the bottom
(captured mid fade-in, hence the light appearance).

## Persistence keys added to `gui_config.json` (under `identify_page`)

- `column_order` - list of logical column indices in the user's chosen order.
- `column_hidden` - list of logical column indices the user chose to hide.

(`column_widths`, `sort_col`, `sort_order`, `status_filter` were already
persisted.)

## Summary

All 30 table/dialog automated checks pass, plus the full application suite
(80 passed, 1 skipped) and `ruff` remain clean. The duplicate navigation icon
is resolved.
