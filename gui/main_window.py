#!/usr/bin/env python3
"""Main application window and entry point for the DVD Episode Identifier GUI.

It wires together the Identify / Build library / Settings / Log pages inside a
dark, acrylic FluentWindow with an icon sidebar.
"""
from __future__ import annotations

import os
import sys
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

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
        try:
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
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
