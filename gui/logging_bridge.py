#!/usr/bin/env python3
"""Logging bridge: route the engine's logging records to a Qt signal so the Log
page (and nothing off the main thread) receives them safely.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Signal, QObject


class LogBridge(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: LogBridge):
        super().__init__()
        self.bridge = bridge

    def emit(self, record):
        try:
            self.bridge.message.emit(self.format(record))
        except Exception:
            pass
