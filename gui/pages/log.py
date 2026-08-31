#!/usr/bin/env python3
"""Log page: live engine output for the current session."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (
    FluentIcon as FIF, PushButton, TextEdit, TitleLabel, CaptionLabel,
)


class LogInterface(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window
        self.setObjectName("logInterface")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Log"))
        root.addWidget(CaptionLabel(
            "Live engine output for the current session."))

        bar = QHBoxLayout()
        bar.addStretch(1)
        clear_btn = PushButton("Clear", self, FIF.DELETE)
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        bar.addWidget(clear_btn)
        root.addLayout(bar)

        self.log_view = TextEdit()
        self.log_view.setReadOnly(True)
        root.addWidget(self.log_view, 1)

    def append(self, line: str):
        self.log_view.append(line)
