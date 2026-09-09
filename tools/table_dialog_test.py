#!/usr/bin/env python3
"""Offscreen verification for Items 4 (results table) and 5 (dialogs).

Run: QT_QPA_PLATFORM="offscreen:configfile=tools/offscreen.json" \
        python tools/table_dialog_test.py
Prints a PASS/FAIL line per check and an OVERALL verdict.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.pages.identify import (  # noqa: E402
    CustomizeViewDialog,
    IdentifyInterface,
    RenamePreviewDialog,
)
from gui.widgets import FlowWidget  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    results: list[str] = []
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        results.append(f"[{'PASS' if cond else 'FAIL'}] {name}")

    win = MainWindow()
    win.show()
    app.processEvents()
    iface: IdentifyInterface = win.identify_interface
    tbl = iface.table
    hdr = tbl.horizontalHeader()

    # --- Item 4.1 eliding ---
    check("table text elide mode = ElideRight",
          tbl.textElideMode() == Qt.ElideRight)
    check("word wrap disabled", tbl.wordWrap() is False)

    # --- Item 4.7 horizontal per-pixel scroll ---
    from PySide6.QtWidgets import QAbstractItemView
    check("horizontal scroll = ScrollPerPixel",
          tbl.horizontalScrollMode() == QAbstractItemView.ScrollPerPixel)
    check("vertical scroll = ScrollPerPixel",
          tbl.verticalScrollMode() == QAbstractItemView.ScrollPerPixel)

    # --- Item 4.6 drag reorder enabled ---
    check("header sections movable", hdr.sectionsMovable())

    # --- Item 4.5 context menu wired ---
    check("header custom context menu policy",
          hdr.contextMenuPolicy() == Qt.CustomContextMenu)

    # --- Item 4.2 tooltip defaults to full text ---
    item = IdentifyInterface._make_text_item("A very long episode title here")
    check("cell tooltip defaults to full text",
          item.toolTip() == "A very long episode title here")
    centered = IdentifyInterface._make_text_item("S01E02", center=True)
    check("short centered cell also has tooltip",
          centered.toolTip() == "S01E02")

    # --- Item 4.3/4.4 auto-hide by width ---
    counts = {
        1200: iface._auto_hide_count(1200),
        980: iface._auto_hide_count(980),
        880: iface._auto_hide_count(880),
        760: iface._auto_hide_count(760),
        600: iface._auto_hide_count(600),
    }
    check("wide (1200px) hides nothing", counts[1200] == 0)
    check("narrow (980px) hides 1", counts[980] == 1)
    check("narrower (600px) hides 4", counts[600] == 4)
    check("auto-hide monotonic as width shrinks",
          counts[1200] <= counts[980] <= counts[880]
          <= counts[760] <= counts[600])

    # essential columns are never in the auto-hide sequence
    check("essential columns never auto-hidden",
          not (set(iface.ESSENTIAL_COLS) & set(iface.AUTO_HIDE_SEQUENCE)))

    # apply a narrow width and confirm essentials stay visible, some hide
    win.resize(820, 640)
    win.stackedWidget.setCurrentWidget(iface)
    for _ in range(4):
        app.processEvents()
    iface._update_auto_hidden()
    app.processEvents()
    essentials_visible = all(not tbl.isColumnHidden(c)
                             for c in iface.ESSENTIAL_COLS)
    some_hidden = any(tbl.isColumnHidden(c)
                      for c in iface.AUTO_HIDE_SEQUENCE)
    check("at 820px: essentials visible", essentials_visible)
    check("at 820px: some non-essential columns auto-hidden", some_hidden)

    # widen again: everything (not user-hidden) comes back
    win.resize(1500, 900)
    for _ in range(4):
        app.processEvents()
    iface._update_auto_hidden()
    app.processEvents()
    all_back = all(not tbl.isColumnHidden(c)
                   for c in range(tbl.columnCount()))
    check("widening restores all auto-hidden columns", all_back)

    # --- Item 4.5 user hide + persistence ---
    iface._toggle_column(iface.C_SUGGESTED, visible=False)
    app.processEvents()
    check("user-hidden column is hidden",
          tbl.isColumnHidden(iface.C_SUGGESTED))
    saved_hidden = iface._ident_get("column_hidden", [])
    check("user hide persisted to config",
          iface.C_SUGGESTED in (saved_hidden or []))
    # essential cannot be hidden via toggle
    iface._toggle_column(iface.C_STATUS, visible=False)
    check("essential column cannot be user-hidden",
          not tbl.isColumnHidden(iface.C_STATUS))
    iface._toggle_column(iface.C_SUGGESTED, visible=True)  # restore

    # --- Item 4.6/4.8 column order persistence round-trip ---
    hdr.moveSection(hdr.visualIndex(iface.C_FILE), 0)
    app.processEvents()
    saved_order = iface._ident_get("column_order", [])
    check("column order persisted", isinstance(saved_order, list)
          and len(saved_order) == tbl.columnCount())
    check("moved column recorded first in order",
          bool(saved_order) and saved_order[0] == iface.C_FILE)

    # build a fresh interface to confirm the saved order is restored
    win2 = MainWindow()
    win2.show()
    app.processEvents()
    hdr2 = win2.identify_interface.table.horizontalHeader()
    restored_order = [hdr2.logicalIndex(i)
                      for i in range(win2.identify_interface.table.columnCount())]
    check("saved order restored in new session",
          restored_order[0] == iface.C_FILE)

    # --- Item 5 dialogs ---
    plan = [{"filename": f"clip_{i}.mkv",
             "dest": str(ROOT / f"Show/Season 01/Show - S01E{i:02d}.mkv")}
            for i in range(1, 60)]
    dlg = RenamePreviewDialog(plan, str(ROOT / "Show"), parent=win)
    dlg.show()
    app.processEvents()
    screen = dlg.screen() or app.primaryScreen()
    avail_h = screen.availableGeometry().height()
    check("RenamePreviewDialog height capped <= 80% screen",
          dlg.widget.maximumHeight() <= int(avail_h * 0.8) + 1)
    # button row wrapped into a FlowWidget
    flow_found = any(isinstance(dlg.buttonLayout.itemAt(i).widget(), FlowWidget)
                     for i in range(dlg.buttonLayout.count())
                     if dlg.buttonLayout.itemAt(i).widget() is not None)
    check("RenamePreviewDialog buttons in a FlowWidget", flow_found)
    check("RenamePreviewDialog both buttons present",
          dlg.yesButton is not None and dlg.cancelButton is not None)
    dlg.close()

    cdlg = CustomizeViewDialog({"options": True, "legend": False,
                                "dry_run": True, "exports": True}, parent=win)
    cdlg.show()
    app.processEvents()
    has_scroll = any(isinstance(cdlg.viewLayout.itemAt(i).widget(), QScrollArea)
                     for i in range(cdlg.viewLayout.count())
                     if cdlg.viewLayout.itemAt(i).widget() is not None)
    check("CustomizeViewDialog content in a QScrollArea", has_scroll)
    cflow = any(isinstance(cdlg.buttonLayout.itemAt(i).widget(), FlowWidget)
                for i in range(cdlg.buttonLayout.count())
                if cdlg.buttonLayout.itemAt(i).widget() is not None)
    check("CustomizeViewDialog buttons in a FlowWidget", cflow)
    check("CustomizeViewDialog height capped",
          cdlg.widget.maximumHeight() <= int(avail_h * 0.8) + 1)
    sel = cdlg.selected_panels()
    check("CustomizeViewDialog reads checkbox state",
          sel.get("options") is True and sel.get("legend") is False)
    cdlg.close()

    print("\n".join(results))
    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
