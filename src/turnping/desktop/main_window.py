"""Main window: sidebar navigation plus the dashboard page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from turnping import APP_NAME, __version__
from turnping.adapters.registry import default_registry
from turnping.core.health import Level
from turnping.core.session_manager import ATTENTION_STATES, SessionState
from turnping.desktop import theme
from turnping.desktop.controller import Controller, DashboardData
from turnping.desktop.history_window import HistoryPage
from turnping.desktop.settings_window import SettingsPage
from turnping.desktop.widgets import (
    Card,
    Pill,
    ago,
    category_label,
    configure_table,
    empty_state,
    plain,
    project_name,
)
from turnping.installation.hooks import IntegrationStatus, StatusReport

PAGES = ["dashboard", "history", "settings"]


class DashboardPage(QWidget):
    open_history = Signal()

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(24, 20, 24, 24)
        self.layout_.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(plain("Dashboard", obj="PageTitle"))
        self.state_pill = Pill("...")
        header.addWidget(self.state_pill)
        header.addStretch(1)
        self.pause_btn = QPushButton("Pause notifications")
        self.pause_btn.setToolTip(
            "Stop showing notifications until you resume (events are still recorded)"
        )
        self.pause_btn.clicked.connect(lambda: controller.set_paused(not controller.paused))
        header.addWidget(self.pause_btn)
        self.test_btn = QPushButton("Send test notification")
        self.test_btn.setProperty("primary", True)
        self.test_btn.setToolTip(
            "Send a labelled test event through the real notification pipeline"
        )
        self.test_btn.clicked.connect(self._send_test)
        header.addWidget(self.test_btn)
        self.layout_.addLayout(header)

        self.loading = plain("Loading status...", muted=True)
        self.layout_.addWidget(self.loading)
        self.banners = QVBoxLayout()
        self.banners.setSpacing(8)
        self.layout_.addLayout(self.banners)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_notif = Card("Notifications")
        self.card_integ = Card("Integrations")
        self.card_sessions = Card("Sessions (24 h)")
        self.card_waiting = Card("Waiting on you")
        for c in (self.card_notif, self.card_integ, self.card_sessions, self.card_waiting):
            cards.addWidget(c)
        self.layout_.addLayout(cards)

        self.layout_.addWidget(plain("Integrations", obj="SectionTitle"))
        self.integ_box = QFrame()
        self.integ_box.setObjectName("Card")
        self.integ_grid = QGridLayout(self.integ_box)
        self.integ_grid.setContentsMargins(14, 10, 14, 10)
        self.integ_grid.setHorizontalSpacing(16)
        self.layout_.addWidget(self.integ_box)

        self.layout_.addWidget(plain("Sessions", obj="SectionTitle"))
        self.sessions_table = QTableWidget()
        configure_table(
            self.sessions_table, ["Agent", "Project", "State", "Last event", "Window", ""]
        )
        self.sessions_table.setMinimumHeight(170)
        self.sessions_empty = empty_state(
            "No sessions yet. Start Claude Code (or another integrated agent) in a terminal; "
            "sessions appear here after their first event."
        )
        self.layout_.addWidget(self.sessions_table)
        self.layout_.addWidget(self.sessions_empty)

        recent_head = QHBoxLayout()
        recent_head.addWidget(plain("Latest events", obj="SectionTitle"))
        recent_head.addStretch(1)
        all_btn = QPushButton("Open history")
        all_btn.clicked.connect(self.open_history.emit)
        recent_head.addWidget(all_btn)
        self.layout_.addLayout(recent_head)
        self.recent_table = QTableWidget()
        configure_table(self.recent_table, ["Time", "Agent", "Event", "Project", "Result"])
        self.recent_table.setMinimumHeight(200)
        self.recent_empty = empty_state(
            "No events recorded yet. Send a test notification to try it out."
        )
        self.layout_.addWidget(self.recent_table)
        self.layout_.addWidget(self.recent_empty)
        self.layout_.addStretch(1)

        controller.paused_changed.connect(lambda _p: self.refresh())

    # ----- actions ----------------------------------------------------------------

    def _send_test(self) -> None:
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Sending...")

        def done(result) -> None:
            self.test_btn.setEnabled(True)
            self.test_btn.setText("Send test notification")
            if result is None:
                QMessageBox.warning(
                    self, APP_NAME, "The test event could not be processed in time."
                )
                return
            delivery = result.delivery
            if delivery is None:
                QMessageBox.information(self, APP_NAME, f"Not shown: {result.decision.reason}.")
            elif not delivery.status.delivered:
                QMessageBox.warning(
                    self, APP_NAME, f"No notification could be shown.\n\n{delivery.detail}"
                )
            self.refresh()

        self.controller.send_test("TASK_COMPLETED", done)

    def refresh(self) -> None:
        self.controller.load_dashboard(self._render)

    # ----- rendering --------------------------------------------------------------

    def _render(self, data: DashboardData) -> None:
        self.loading.hide()
        paused = data.paused
        self.state_pill.set("Paused" if paused else "Active", "warn" if paused else "ok")
        self.pause_btn.setText("Resume notifications" if paused else "Pause notifications")
        self._render_banners(data)

        notif = next((c for c in data.checks if c.name == "Windows notifications"), None)
        mode = self.controller.config.general.notification_backend
        if notif and notif.level is Level.OK and mode != "popup":
            self.card_notif.set("Windows toasts", "Native notifications are enabled", "ok")
        elif mode == "toast":
            self.card_notif.set("Unavailable", notif.detail if notif else "", "error")
        else:
            self.card_notif.set(
                "TurnPing popups",
                "Windows toasts are off, so TurnPing shows its own popups",
                "warn",
            )

        installed = [
            r
            for r in data.integrations
            if r.status in (IntegrationStatus.OPERATIONAL, IntegrationStatus.INSTALLED_UNVERIFIED)
        ]
        broken = [r for r in data.integrations if r.status is IntegrationStatus.MISCONFIGURED]
        self.card_integ.set(
            f"{len(installed)} of {len(data.integrations)}",
            f"{len(broken)} need attention" if broken else "installed",
            "error" if broken else ("ok" if installed else "info"),
        )
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recent_sessions = [
            s for s in data.sessions if s.last_seen >= cutoff and s.provider != "turnping"
        ]
        live = [s for s in recent_sessions if s.state != SessionState.ENDED.value]
        self.card_sessions.set(str(len(live)), f"{len(recent_sessions)} seen in the last 24 h")
        waiting = [s for s in live if SessionState(s.state) in ATTENTION_STATES]
        self.card_waiting.set(
            str(len(waiting)),
            f"{data.pending_reminders} reminder(s) scheduled"
            if data.pending_reminders
            else "nothing pending",
            "warn" if waiting else "ok",
        )
        cards = (self.card_notif, self.card_integ, self.card_sessions, self.card_waiting)
        tallest = max(
            (c.heightForWidth(c.width()) if c.hasHeightForWidth() else c.sizeHint().height())
            for c in cards
        )
        for c in cards:
            c.setMinimumHeight(max(tallest, c.sizeHint().height()))
        self._render_integrations(data.integrations)
        self._render_sessions(data)
        self._render_recent(data)

    def _render_banners(self, data: DashboardData) -> None:
        while self.banners.count():
            item = self.banners.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        problems = [c for c in data.checks if c.level in (Level.WARN, Level.ERROR)]
        notes: list[tuple[str, str, str, str | None]] = []
        if data.recovery_note:
            notes.append(("error", "Database recovered", data.recovery_note, None))
        if data.config_error:
            notes.append(
                (
                    "error",
                    "Configuration file is invalid",
                    f"{data.config_error}. TurnPing is using defaults; saving Settings replaces the "
                    "file (a copy of the broken file is kept).",
                    None,
                )
            )
        for c in problems:
            notes.append(("error" if c.level is Level.ERROR else "warn", c.name, c.detail, c.fix))
        for level, title, detail, fix in notes:
            banner = QFrame()
            banner.setObjectName("BannerError" if level == "error" else "Banner")
            row = QHBoxLayout(banner)
            row.setContentsMargins(14, 10, 14, 10)
            text = QVBoxLayout()
            text.addWidget(plain(title, obj="SectionTitle"))
            text.addWidget(plain(detail + (f"\n{fix}" if fix else ""), muted=True, wrap=True))
            row.addLayout(text, 1)
            if title == "Windows notifications":
                btn = QPushButton("Open notification settings")
                btn.setToolTip("Opens Windows Settings > System > Notifications")
                btn.clicked.connect(
                    lambda: QDesktopServices.openUrl(QUrl("ms-settings:notifications"))
                )
                row.addWidget(btn)
            self.banners.addWidget(banner)

    def _render_integrations(self, reports: list[StatusReport]) -> None:
        while self.integ_grid.count():
            item = self.integ_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        level_for = {
            IntegrationStatus.OPERATIONAL: "ok",
            IntegrationStatus.INSTALLED_UNVERIFIED: "info",
            IntegrationStatus.NOT_INSTALLED: "muted",
            IntegrationStatus.MISCONFIGURED: "error",
            IntegrationStatus.UNSUPPORTED: "warn",
        }
        for row, r in enumerate(reports):
            name = plain(r.display_name)
            name.setStyleSheet("font-weight: 600;")
            status = plain(r.status.value)
            status.setStyleSheet(
                f"color: {theme.level_color(level_for[r.status])}; font-weight: 600;"
            )
            detail = plain(r.detail, muted=True, wrap=True)
            detail.setToolTip(f"Config file: {r.path}")
            self.integ_grid.addWidget(name, row, 0)
            self.integ_grid.addWidget(status, row, 1)
            self.integ_grid.addWidget(detail, row, 2)
            installed = r.status is not IntegrationStatus.NOT_INSTALLED
            btn = QPushButton(
                "Remove"
                if installed and r.status is not IntegrationStatus.MISCONFIGURED
                else "Repair"
                if r.status is IntegrationStatus.MISCONFIGURED
                else "Install"
            )
            btn.setEnabled(r.agent_detected or installed)
            btn.setToolTip(
                f"{'Remove TurnPing hooks from' if btn.text() == 'Remove' else 'Add TurnPing hooks to'} {r.path} "
                "(a backup is made first; other settings are preserved)"
                if btn.isEnabled()
                else f"{r.display_name} is not installed on this machine"
            )
            btn.clicked.connect(lambda _=False, rep=r, b=btn: self._integration_action(rep, b))
            self.integ_grid.addWidget(btn, row, 3)
        self.integ_grid.setColumnStretch(2, 1)

    def _integration_action(self, report: StatusReport, button: QPushButton) -> None:
        removing = button.text() == "Remove"
        if (
            removing
            and QMessageBox.question(
                self,
                APP_NAME,
                f"Remove TurnPing hooks from {report.display_name}?\n\n{report.path}\n\n"
                "Only TurnPing's entries are removed; a backup is kept.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        button.setEnabled(False)

        def done(result) -> None:
            box = QMessageBox.information if result.ok else QMessageBox.warning
            box(self, APP_NAME, result.render())
            self.refresh()

        if removing:
            self.controller.uninstall_integration(report.provider, done)
        else:
            self.controller.install_integration(report.provider, done)

    def _render_sessions(self, data: DashboardData) -> None:
        sessions = [s for s in data.sessions if s.provider != "turnping"]
        self.sessions_table.setVisible(bool(sessions))
        self.sessions_empty.setVisible(not sessions)
        self.sessions_table.setRowCount(len(sessions))
        registry = default_registry()
        for row, s in enumerate(sessions):
            state = SessionState(s.state)
            values = [
                registry.display_name(s.provider),
                project_name(s.project_path),
                state.label,
                ago(s.last_seen),
                (s.host.process_name or s.host.terminal or "-") if s.host else "-",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1:
                    tip = s.project_path or ""
                    if s.session_id:
                        tip += f"\nsession {s.session_id}"
                    elif s.key_inferred:
                        tip += "\nThis agent did not report a session id; grouped by folder."
                    item.setToolTip(tip)
                if col == 2:
                    level = (
                        "warn"
                        if state in ATTENTION_STATES
                        else "error"
                        if state is SessionState.FAILED
                        else None
                    )
                    if level:
                        item.setForeground(QColor(theme.level_color(level)))
                self.sessions_table.setItem(row, col, item)
            btn = QPushButton("Open")
            btn.setToolTip("Bring this session's terminal or editor window to the front")
            btn.clicked.connect(lambda _=False, sess=s: self.controller.open_session(sess))
            self.sessions_table.setCellWidget(row, 5, btn)
        self.sessions_table.resizeColumnsToContents()
        header = self.sessions_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(5, header.ResizeMode.Fixed)
        self.sessions_table.setColumnWidth(5, 96)

    def _render_recent(self, data: DashboardData) -> None:
        records = data.recent
        self.recent_table.setVisible(bool(records))
        self.recent_empty.setVisible(not records)
        self.recent_table.setRowCount(len(records))
        registry = default_registry()
        for row, r in enumerate(records):
            result = r.decision if r.decision != "notified" else (r.delivery_status or "notified")
            values = [
                ago(r.received_at),
                registry.display_name(r.provider),
                category_label(r.category) + (" (reminder)" if r.is_reminder else ""),
                project_name(r.project_path),
                result,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 4:
                    item.setToolTip(r.delivery_detail or r.decision_reason or "")
                self.recent_table.setItem(row, col, item)
        self.recent_table.resizeColumnsToContents()


class MainWindow(QMainWindow):
    quit_requested = Signal()

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1080, 760)
        self.setMinimumSize(QSize(820, 560))
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 18, 0, 14)
        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 16, 0)
        logo = QLabel()
        logo.setPixmap(theme.draw_logo(34))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(plain(APP_NAME, obj="BrandName"))
        names.addWidget(plain("One ping. Every agent.", obj="Tagline"))
        brand.addLayout(names)
        brand.addStretch(1)
        side.addLayout(brand)
        side.addSpacing(16)
        self.nav = QListWidget()
        self.nav.setAccessibleName("Navigation")
        for label in ("Dashboard", "History", "Settings"):
            QListWidgetItem(label, self.nav)
        side.addWidget(self.nav, 1)
        footer = plain(f"v{__version__}  ·  local only, no telemetry", muted=True, wrap=True)
        footer.setContentsMargins(16, 0, 16, 0)
        side.addWidget(footer)
        hint = plain(
            "Closing this window keeps TurnPing running in the tray. Use Exit to quit.",
            muted=True,
            wrap=True,
        )
        hint.setContentsMargins(16, 6, 16, 0)
        side.addWidget(hint)
        layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(controller)
        self.history = HistoryPage(controller)
        self.settings = SettingsPage(controller)
        for page in (self.dashboard, self.history, self.settings):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)
        self.dashboard.open_history.connect(lambda: self.show_page("history"))

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(400)  # debounce bursts of events
        self._refresh_timer.timeout.connect(self._refresh_current)
        controller.event_processed.connect(lambda _r: self._refresh_timer.start())
        self._clock = QTimer(self)
        self._clock.setInterval(30_000)  # keep "x min ago" fresh
        self._clock.timeout.connect(self._refresh_current)
        self._clock.start()

        for i in range(3):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda i=i: self.nav.setCurrentRow(i))
        QShortcut(QKeySequence("F5"), self, self._refresh_current)

    def _page_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self._refresh_current()

    def _refresh_current(self) -> None:
        if not self.isVisible():
            return
        page = self.stack.currentWidget()
        if page is self.dashboard:
            self.dashboard.refresh()
        elif page is self.history:
            self.history.refresh()

    def show_page(self, name: str) -> None:
        self.nav.setCurrentRow(PAGES.index(name) if name in PAGES else 0)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._refresh_current()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.controller.config.general.close_to_tray:
            event.ignore()
            self.hide()
            self.controller.status_message.emit("TurnPing is still running in the tray.")
        else:
            event.accept()
            self.quit_requested.emit()
