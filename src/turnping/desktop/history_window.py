"""Notification history: searchable, filterable, clearable."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from turnping import APP_NAME
from turnping.adapters.registry import default_registry
from turnping.contracts.events import EventCategory
from turnping.desktop.controller import Controller
from turnping.desktop.widgets import (
    category_label,
    configure_table,
    empty_state,
    local_time,
    plain,
    project_name,
)
from turnping.storage.repository import EventRecord


class HistoryPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("Page")
        self.records: list[EventRecord] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(plain("Notification history", obj="PageTitle"))
        head.addStretch(1)
        self.count_label = plain("", muted=True)
        head.addWidget(self.count_label)
        clear = QPushButton("Clear history...")
        clear.setToolTip("Delete all recorded events and sessions from this computer")
        clear.clicked.connect(self._clear)
        head.addWidget(clear)
        layout.addLayout(head)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search messages, projects, session ids...")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search history")
        self.provider = QComboBox()
        self.provider.setAccessibleName("Filter by agent")
        self.provider.addItem("All agents", None)
        registry = default_registry()
        for pid in ("claude-code", "codex", "gemini-cli", "turnping"):
            self.provider.addItem(
                registry.display_name(pid) + (" (tests)" if pid == "turnping" else ""), pid
            )
        self.category = QComboBox()
        self.category.setAccessibleName("Filter by event type")
        self.category.addItem("All events", None)
        for c in EventCategory:
            self.category.addItem(c.label, c.value)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.provider)
        filters.addWidget(self.category)
        layout.addLayout(filters)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.provider.currentIndexChanged.connect(lambda _i: self.refresh())
        self.category.currentIndexChanged.connect(lambda _i: self.refresh())

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget()
        configure_table(
            self.table, ["Time", "Agent", "Event", "Project", "Session", "Result", "Message"]
        )
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.doubleClicked.connect(lambda _i: self._open_selected())
        self.empty = empty_state("No events match. History is stored only on this computer.")
        top = QWidget()
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.addWidget(self.table)
        top_l.addWidget(self.empty)
        splitter.addWidget(top)

        self.detail = QFrame()
        self.detail.setObjectName("Card")
        detail_l = QVBoxLayout(self.detail)
        detail_l.setContentsMargins(14, 10, 14, 10)
        self.detail_form = QFormLayout()
        detail_l.addLayout(self.detail_form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.open_btn = QPushButton("Open session")
        self.open_btn.setToolTip("Bring this session's terminal or editor window to the front")
        self.open_btn.clicked.connect(self._open_selected)
        self.open_btn.setEnabled(False)
        buttons.addWidget(self.open_btn)
        detail_l.addLayout(buttons)
        splitter.addWidget(self.detail)
        splitter.setSizes([420, 180])
        layout.addWidget(splitter, 1)

    def refresh(self) -> None:
        text = self.search.text().strip() or None
        self.controller.load_history(
            self._render,
            provider=self.provider.currentData(),
            category=self.category.currentData(),
            search=text,
        )

    def _render(self, records: list[EventRecord]) -> None:
        selected = self._selected()
        self.records = records
        self.table.setVisible(bool(records))
        self.empty.setVisible(not records)
        self.count_label.setText(
            f"{len(records)} shown" + (" (newest 1000)" if len(records) >= 1000 else "")
        )
        self.table.setRowCount(len(records))
        registry = default_registry()
        for row, r in enumerate(records):
            result = r.decision if r.decision != "notified" else (r.delivery_status or "notified")
            if r.dismissed:
                result += ", dismissed"
            values = [
                local_time(r.received_at),
                registry.display_name(r.provider),
                category_label(r.category) + (" (reminder)" if r.is_reminder else ""),
                project_name(r.project_path),
                (r.session_id or "-")[:12],
                result,
                r.message or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, r.event_id)
                if col == 5:
                    item.setToolTip(r.delivery_detail or r.decision_reason or "")
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if selected:
            for row, r in enumerate(records):
                if r.event_id == selected.event_id:
                    self.table.selectRow(row)
                    break
        self._show_detail()

    def _selected(self) -> EventRecord | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.records):
            return None
        return self.records[rows[0].row()]

    def _show_detail(self) -> None:
        while self.detail_form.rowCount():
            self.detail_form.removeRow(0)
        r = self._selected()
        self.open_btn.setEnabled(r is not None)
        if r is None:
            self.detail_form.addRow(plain("Select an event to see its details.", muted=True))
            return
        rows = [
            ("Received", local_time(r.received_at)),
            ("Agent event", f"{r.provider} · {r.native_type}"),
            ("Project", r.project_path or "not reported"),
            ("Session id", r.session_id or "not reported by the agent"),
            ("Decision", f"{r.decision}: {r.decision_reason or ''}"),
            (
                "Delivery",
                f"{r.delivery_status or '-'}"
                + (f": {r.delivery_detail}" if r.delivery_detail else ""),
            ),
        ]
        if r.message:
            rows.append(("Message", r.message))
        if r.metadata:
            rows.append(("Details", ", ".join(f"{k}={v}" for k, v in r.metadata.items())))
        for label, value in rows:
            v = plain(value, wrap=True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_form.addRow(plain(label + ":", muted=True), v)

    def _open_selected(self) -> None:
        r = self._selected()
        if r is not None:
            self.controller.open_event(r.event_id)

    def _clear(self) -> None:
        if (
            QMessageBox.question(
                self,
                APP_NAME,
                "Delete all notification history and session records from this computer?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def done(count: int) -> None:
            self.controller.status_message.emit(f"Deleted {count} history entries.")
            self.refresh()

        self.controller.clear_history(done)
