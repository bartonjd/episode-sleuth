#!/usr/bin/env python3
"""Small reusable widgets shared across the GUI pages."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    LineEdit,
    PushButton,
    StrongBodyLabel,
    ToolButton,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF


class FlowLayout(QLayout):
    """A layout that arranges child widgets left-to-right and wraps to the next
    line when it runs out of horizontal space.

    This makes button bars adaptive: on wide windows the buttons sit in a
    single horizontal row; as the window narrows they wrap onto more lines and,
    at the narrowest, stack vertically one per line. Crucially, its minimum
    width is only a single item wide, so it never forces the whole page to stay
    wide the way a plain horizontal box layout would.
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            next_x = x + item.sizeHint().width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + item.sizeHint().width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()


class FlowWidget(QWidget):
    """A QWidget that hosts a FlowLayout and keeps its own minimum height in
    sync with the wrapped content.

    A FlowLayout reports a single-row height as its size hint (so the window
    can shrink freely), which means a parent layout would only reserve one
    row of space and clip any wrapped rows. Updating minimumHeight from the
    layout's heightForWidth() on every resize guarantees every wrapped row is
    given room, no matter how deeply nested the widget is (e.g. inside a
    QSplitter that does not propagate heightForWidth on its own).
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._flow = FlowLayout(self, margin=margin, spacing=spacing)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def addWidget(self, widget: QWidget) -> None:
        self._flow.addWidget(widget)

    def clear(self) -> None:
        """Remove and delete every widget currently in the flow."""
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    @property
    def flow(self) -> FlowLayout:
        return self._flow

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(self._flow.heightForWidth(self.width()))


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

    # Emitted after the collapsed state changes; the argument is the new
    # collapsed flag. Consumers use this to re-flow surrounding layout (e.g.
    # re-assert a splitter pane's minimum height so content never clips).
    toggled = Signal(bool)

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
        self.toggled.emit(self._collapsed)

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_density(self, margins: tuple[int, int, int, int], spacing: int):
        """Apply view-density spacing to the card's content layout."""
        self._outer.setContentsMargins(margins[0], margins[1], margins[2], margins[3])
        self._v.setSpacing(spacing)


class TagInputWidget(QWidget):
    """An editable set of "chips" (tags), each removable via a small x button.

    New tags are added by typing in the line edit and pressing Enter or the
    Add button. Chips wrap onto multiple lines as needed. An optional
    ``normalizer`` callback cleans each raw entry (e.g. lowercasing an
    extension and forcing a leading dot); returning an empty string rejects
    the entry. Duplicate tags are ignored.
    """

    changed = Signal()

    def __init__(self, parent=None, placeholder: str = "",
                 normalizer=None, add_text: str = "Add"):
        super().__init__(parent)
        self._tags: list[str] = []
        self._normalizer = normalizer or (lambda s: s.strip())

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # Chips area (wraps).
        self._chips = FlowWidget(self, spacing=6)
        v.addWidget(self._chips)

        # Input row: line edit + Add button.
        row = QHBoxLayout()
        row.setSpacing(8)
        self._edit = LineEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.setClearButtonEnabled(True)
        self._edit.returnPressed.connect(self._on_add)
        self._add_btn = PushButton(add_text, self, FIF.ADD)
        self._add_btn.clicked.connect(self._on_add)
        row.addWidget(self._edit, 1)
        row.addWidget(self._add_btn)
        v.addLayout(row)

    # -- public API --
    def tags(self) -> list[str]:
        """Return the current tags in insertion order."""
        return list(self._tags)

    def text(self) -> str:
        """Return the tags as a comma-separated string."""
        return ", ".join(self._tags)

    def set_tags(self, value) -> None:
        """Replace all tags from a list or a comma/space-separated string."""
        if isinstance(value, str):
            raw = [p for p in value.replace(",", " ").split()]
        else:
            raw = list(value or [])
        self._tags = []
        for item in raw:
            norm = self._normalizer(str(item))
            if norm and norm not in self._tags:
                self._tags.append(norm)
        self._rebuild()

    # -- internals --
    def _on_add(self) -> None:
        norm = self._normalizer(self._edit.text())
        self._edit.clear()
        self._edit.setFocus()
        if not norm or norm in self._tags:
            return
        self._tags.append(norm)
        self._rebuild()

    def _remove(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._rebuild()

    def _rebuild(self) -> None:
        self._chips.clear()
        for tag in self._tags:
            self._chips.addWidget(self._make_chip(tag))
        self._chips.updateGeometry()
        self._chips.setMinimumHeight(
            self._chips.flow.heightForWidth(max(self._chips.width(), 1)))
        self.changed.emit()

    def _make_chip(self, tag: str) -> QWidget:
        chip = QFrame()
        chip.setObjectName("tagChip")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(12, 3, 4, 3)
        lay.setSpacing(2)
        lbl = BodyLabel(tag)
        close = TransparentToolButton(FIF.CLOSE, chip)
        close.setFixedSize(20, 20)
        close.setIconSize(QSize(9, 9))
        close.setToolTip(f"Remove {tag}")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda: self._remove(tag))
        lay.addWidget(lbl)
        lay.addWidget(close)
        # A subtle pill that reads on both light and dark themes.
        chip.setStyleSheet(
            "#tagChip { background-color: rgba(128, 128, 128, 0.20);"
            " border-radius: 12px; }")
        return chip


def _path_row(placeholder: str):
    """A LineEdit that expands to fill available width."""
    le = LineEdit()
    le.setPlaceholderText(placeholder)
    le.setClearButtonEnabled(True)
    le.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return le
