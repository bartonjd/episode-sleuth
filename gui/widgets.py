#!/usr/bin/env python3
"""Small reusable widgets shared across the GUI pages."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QSizePolicy

from qfluentwidgets import CardWidget, StrongBodyLabel, LineEdit


class Card(CardWidget):
    """A rounded card with a title and a vertical content area."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(20, 16, 20, 16)
        self._v.setSpacing(12)
        if title:
            self._v.addWidget(StrongBodyLabel(title))

    def add(self, *widgets, spacing: int = 8):
        row = QHBoxLayout()
        row.setSpacing(spacing)
        for w in widgets:
            row.addWidget(w)
        self._v.addLayout(row)
        return row

    def addLayout(self, layout):
        self._v.addLayout(layout)

    def addWidget(self, w):
        self._v.addWidget(w)


def _path_row(placeholder: str):
    """A LineEdit that expands to fill available width."""
    le = LineEdit()
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return le
