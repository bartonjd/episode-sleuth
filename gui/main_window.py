#!/usr/bin/env python3
"""Main application window and entry point for the EpisodeSleuth GUI.

It wires together the Identify / Build library / Settings / Log pages inside a
dark, acrylic FluentWindow with an icon sidebar.
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# Custom application icon (256x256 .ico bundled under packaging/). Shared by the
# main window title bar, the Windows taskbar, and the QApplication default.
APP_ICON_PATH = Path(__file__).resolve().parent.parent / "packaging" / "app.ico"

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    setTheme, setThemeColor, Theme,
)

from gui_config import GuiConfig

from audio_fingerprint.gui.constants import (
    APP_TITLE, DEFAULT_DB, DEFAULT_CONFIG, HERE,
    COLOR_OK, COLOR_MEDIUM, COLOR_REVIEW,
)
from audio_fingerprint.gui.logging_bridge import LogBridge, QtLogHandler
from audio_fingerprint.gui.pages.identify import IdentifyInterface
from audio_fingerprint.gui.pages.build import BuildInterface
from audio_fingerprint.gui.pages.settings import SettingsInterface
from audio_fingerprint.gui.pages.log import LogInterface


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.gui_cfg = GuiConfig()

        # theme first so child widgets pick up the palette
        self.apply_theme(self.gui_cfg.get("theme", "Dark"))
        setThemeColor(self.gui_cfg.get("theme_color", "#0078d4"))

        self.resize(1080, 760)
        self.setWindowTitle(APP_TITLE)
        # Prefer the bundled custom icon; fall back to a Fluent icon if missing.
        try:
            if APP_ICON_PATH.exists():
                self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
                # Make Windows group the taskbar entry under our own AppUserModelID
                # so the taskbar shows the custom icon instead of the generic
                # Python one. Harmless / no-op on non-Windows platforms.
                if sys.platform == "win32":
                    try:
                        import ctypes
                        myappid = "abacusai.episode_sleuth.dvd_identifier.1"
                        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                            myappid)
                    except Exception:
                        pass
            else:
                self.setWindowIcon(QIcon(FIF.ALBUM.path()))
        except Exception:
            pass

        # logging bridge -> Log page
        self.log_bridge = LogBridge()
        self._install_logging()

        # pages
        self.identify_interface = IdentifyInterface(self)
        self.build_interface = BuildInterface(self)
        self.settings_interface = SettingsInterface(self)
        self.log_interface = LogInterface(self)
        self.log_bridge.message.connect(self.log_interface.append)

        self.addSubInterface(self.identify_interface, FIF.SEARCH, "Identify")
        self.addSubInterface(self.build_interface, FIF.LIBRARY, "Build library")
        self.addSubInterface(self.settings_interface, FIF.SETTING, "Settings",
                             NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.log_interface, FIF.COMMAND_PROMPT, "Log",
                             NavigationItemPosition.BOTTOM)

        self.navigationInterface.setExpandWidth(220)

        # Guard against leaving the Settings tab with unsaved changes (or a live
        # model download). We track the current interface ourselves because the
        # stacked widget's currentChanged fires *after* the switch, so if the
        # user cancels we switch straight back to Settings.
        self._current_iface = self.stackedWidget.currentWidget()
        self._nav_guard_active = False
        self.stackedWidget.currentChanged.connect(self._on_interface_changed)

    def _on_interface_changed(self, index: int):
        """Intercept tab switches to protect unsaved Settings changes."""
        if self._nav_guard_active:
            return
        new_widget = self.stackedWidget.widget(index)
        prev = self._current_iface
        # Only guard when leaving the Settings tab for a different tab.
        if (prev is self.settings_interface
                and new_widget is not self.settings_interface):
            if not self.settings_interface.confirm_leave():
                # User chose to stay - switch straight back to Settings.
                self._nav_guard_active = True
                try:
                    self.switchTo(self.settings_interface)
                    self.navigationInterface.setCurrentItem(
                        self.settings_interface.objectName())
                finally:
                    self._nav_guard_active = False
                return
        self._current_iface = new_widget

    def closeEvent(self, event):
        # If a model download is still running, cancel it and let the thread
        # unwind cleanly so we never tear down a live QThread on exit.
        worker = getattr(self.settings_interface, "_dl_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(5000)
        super().closeEvent(event)

    # ----- helpers used by pages -----
    def current_db_path(self) -> str:
        p = self.gui_cfg.get("db_path", "")
        if p:
            return p
        return DEFAULT_DB if os.path.exists(DEFAULT_DB) else ""

    def current_engine_config(self) -> str:
        return self.gui_cfg.get("engine_config_path", "")

    def apply_theme(self, name: str):
        mapping = {"Dark": Theme.DARK, "Light": Theme.LIGHT, "Auto": Theme.AUTO}
        setTheme(mapping.get(name, Theme.DARK))

    def _install_logging(self):
        handler = QtLogHandler(self.log_bridge)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers = [h for h in root.handlers if not isinstance(h, QtLogHandler)]
        root.addHandler(handler)


def main():
    # Base console logging config (the GUI adds its own Qt log bridge on top
    # in MainWindow._install_logging).
    from logging_config import setup_logging
    setup_logging(logging.INFO)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    # Set the app-wide default icon (used for any window that does not set its
    # own, and as the fallback taskbar icon).
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
