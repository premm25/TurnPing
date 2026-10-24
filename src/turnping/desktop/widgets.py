"""Small shared widgets and formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from turnping.desktop import theme


def plain(text: str, *, muted: bool = False, wrap: bool = False, obj: str | None = None) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)  # never interpret event text as rich text
    if muted:
        label.setProperty("muted", True)
    if wrap:
        label.setWordWrap(True)
    if obj:
        label.setObjectName(obj)
    return label


def ago(moment: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    seconds = int((now - moment).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return moment.astimezone().strftime("%b %d, %H:%M")


def local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def category_label(category: str) -> str:
    from turnping.contracts.events import EventCategory

    try:
        return EventCategory(category).label
    except ValueError:
        return category


def project_name(path: str | None) -> str:
    if not path:
        return "-"
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path


class Card(QFrame):
    def __init__(self, title: str, value: str = "...", detail: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.title = plain(title, muted=True)
        self.value = plain(value, obj="CardValue")
        self.detail = plain(detail, muted=True, wrap=True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addStretch(1)  # keep content at the top when cards are equalized

    def set(self, value: str, detail: str = "", level: str | None = None) -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        color = theme.level_color(level) if level else ""
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.setToolTip(detail)


class Pill(QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setObjectName("Pill")

    def set(self, text: str, level: str) -> None:
        p = theme.current()
        color = theme.level_color(level, p)
        self.setText(text)
        self.setStyleSheet(f"color: {color}; border: 1px solid {color}; background: transparent;")


def empty_state(text: str) -> QWidget:
    box = QFrame()
    box.setObjectName("Card")
    layout = QHBoxLayout(box)
    layout.setContentsMargins(16, 18, 16, 18)
    label = plain(text, muted=True, wrap=True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    return box


def configure_table(table: QTableWidget, headers: list[str]) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)  # room for in-row buttons
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setHighlightSections(False)
