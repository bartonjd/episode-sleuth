#!/usr/bin/env python3
"""Small reusable widgets shared across the GUI pages."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CardWidget, LineEdit, StrongBodyLabel, ToolButton
from qfluentwidgets import FluentIcon as FIF


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

    def set_density(self, margins: tuple[int, int, int, int], spacing: int):
        """Apply view-density spacing to the card's content layout."""
        self._v.setContentsMargins(*margins)
        self._v.setSpacing(spacing)


class CollapsibleCard(CardWidget):
    """A rounded card whose body can be collapsed behind a header toggle.

    The header shows the title plus a chevron button; clicking either the
    button toggles the visibility of the body content. Content is added the
    same way as :class:`Card` (``add`` / ``addLayout`` / ``addWidget``).
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._collapsed = False

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(20, 12, 20, 12)
        self._outer.setSpacing(8)

        # Header row: title label + stretch + chevron toggle button.
        header = QHBoxLayout()
        header.setSpacing(8)
        self._title_label = StrongBodyLabel(title or "")
        self._toggle_btn = ToolButton(FIF.CHEVRON_DOWN_MED)
        self._toggle_btn.setToolTip("Collapse or expand this section")
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self.toggle)
        header.addWidget(self._title_label)
        header.addStretch(1)
        header.addWidget(self._toggle_btn)
        self._outer.addLayout(header)

        # Body container holds the actual content.
        self._body = QWidget(self)
        self._v = QVBoxLayout(self._body)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(12)
        self._outer.addWidget(self._body)

    # -- content proxies (mirror Card's API) --
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

    # -- collapse control --
    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._body.setVisible(not self._collapsed)
        self._toggle_btn.setIcon(
            FIF.CHEVRON_RIGHT_MED if self._collapsed else FIF.CHEVRON_DOWN_MED
        )

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_density(self, margins: tuple[int, int, int, int], spacing: int):
        """Apply view-density spacing to the card's content layout."""
        self._outer.setContentsMargins(margins[0], margins[1], margins[2], margins[3])
        self._v.setSpacing(spacing)


def _path_row(placeholder: str):
    """A LineEdit that expands to fill available width."""
    le = LineEdit()
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return le
