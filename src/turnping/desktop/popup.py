"""TurnPing's own on-screen popups: the fallback when Windows toasts are unavailable.

The backend is called on the engine thread; it hands the notification to the GUI thread
through a queued Qt signal and reports DISPATCHED (a window was requested; TurnPing
cannot prove the user looked at it).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from turnping.contracts.events import Priority
from turnping.desktop import theme
from turnping.notifications.base import (
    BackendStatus,
    DeliveryResult,
    DeliveryStatus,
    Notification,
    NotificationBackend,
)

MAX_VISIBLE = 4
TIMEOUT_MS = {Priority.LOW: 6000, Priority.NORMAL: 9000, Priority.HIGH: 25000, Priority.URGENT: 0}


class PopupBridge(QObject):
    show_requested = Signal(object)
    withdraw_requested = Signal(str)


class PopupBackend(NotificationBackend):
    name = "turnping-popup"

    def __init__(self, bridge: PopupBridge) -> None:
        self.bridge = bridge
        self.enabled = True

    def status(self) -> BackendStatus:
        if not self.enabled:
            return BackendStatus(False, "TurnPing popups are not available")
        return BackendStatus(True, "TurnPing popup windows (desktop app running)")

    def show(self, notification: Notification) -> DeliveryResult:
        if not self.enabled:
            return DeliveryResult(
                DeliveryStatus.UNAVAILABLE, self.name, "desktop app shutting down"
            )
        self.bridge.show_requested.emit(notification)
        return DeliveryResult(DeliveryStatus.DISPATCHED, self.name, "shown as an TurnPing popup")

    def withdraw(self, event_id: str) -> None:
        self.bridge.withdraw_requested.emit(event_id)


class PopupWidget(QFrame):
    opened = Signal(str)
    dismissed = Signal(str)

    def __init__(self, n: Notification) -> None:
        super().__init__(None)
        self.notification = n
        self.setObjectName("Popup")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(380)
        self.setAccessibleName(f"TurnPing notification: {n.title}")
        p = theme.current()
        accent = {
            Priority.URGENT: p.err,
            Priority.HIGH: p.warn,
        }.get(n.priority, p.accent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 12, 0)
        bar = QFrame()
        bar.setObjectName("PopupAccent")
        bar.setFixedWidth(5)
        bar.setStyleSheet(
            f"background: {accent}; border-top-left-radius: 12px; border-bottom-left-radius: 12px;"
        )
        outer.addWidget(bar)
        body = QVBoxLayout()
        body.setContentsMargins(10, 12, 0, 12)
        body.setSpacing(4)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.draw_logo(20))
        head.addWidget(logo)
        title = QLabel(n.title)
        title.setObjectName("PopupTitle")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        body.addLayout(head)
        message = QLabel(n.body)
        message.setWordWrap(True)
        message.setTextFormat(Qt.TextFormat.PlainText)  # payload text is never rendered as HTML
        title.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(message)
        attribution = QLabel(n.attribution)
        attribution.setTextFormat(Qt.TextFormat.PlainText)
        attribution.setProperty("muted", True)
        body.addWidget(attribution)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        dismiss = QPushButton("Dismiss")
        dismiss.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # popups never take focus; Esc dismisses
        dismiss.setToolTip("Hide this notification (Esc)")
        dismiss.clicked.connect(self._dismiss)
        open_btn = QPushButton("Open session")
        open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_btn.setProperty("primary", True)
        open_btn.setToolTip("Return to the terminal or editor running this session")
        open_btn.clicked.connect(self._open)
        buttons.addWidget(dismiss)
        buttons.addWidget(open_btn)
        body.addLayout(buttons)
        outer.addLayout(body, 1)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._dismiss)

        timeout = TIMEOUT_MS.get(n.priority, 9000)  # 0 = stays until the user acts
        if timeout:
            QTimer.singleShot(timeout, self.close)

    def _open(self) -> None:
        self.opened.emit(self.notification.event_id)
        self.close()

    def _dismiss(self) -> None:
        self.dismissed.emit(self.notification.event_id)
        self.close()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.childAt(
            event.position().toPoint()
        ):
            self._open()
        super().mousePressEvent(event)


class PopupManager(QObject):
    """Stacks popups in the bottom-right corner of the primary screen."""

    open_event = Signal(str)
    dismiss_event = Signal(str)

    def __init__(self, bridge: PopupBridge) -> None:
        super().__init__()
        self._popups: list[PopupWidget] = []
        bridge.show_requested.connect(self.show_notification)
        bridge.withdraw_requested.connect(self.withdraw)

    def show_notification(self, n: Notification) -> None:
        if n.session_key:
            # A newer alert for the same session replaces the older popup.
            for old in [w for w in self._popups if w.notification.session_key == n.session_key]:
                old.close()
        while len(self._popups) >= MAX_VISIBLE:
            self._popups[0].close()
        widget = PopupWidget(n)
        widget.opened.connect(self.open_event)
        widget.dismissed.connect(self.dismiss_event)
        widget.destroyed.connect(lambda _=None, w=widget: self._forget(w))
        self._popups.append(widget)
        widget.adjustSize()
        widget.show()
        self._layout()

    def withdraw(self, event_id: str) -> None:
        for w in list(self._popups):
            if w.notification.event_id == event_id:
                w.close()

    def close_all(self) -> None:
        for w in list(self._popups):
            w.close()

    def _forget(self, widget: PopupWidget) -> None:
        if widget in self._popups:
            self._popups.remove(widget)
        self._layout()

    def _layout(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        y = area.bottom() - 16
        for w in reversed(self._popups):
            try:
                h = w.sizeHint().height()
                y -= h
                w.move(QPoint(area.right() - w.width() - 16, y))
                y -= 10
            except RuntimeError:
                continue  # widget already deleted

    @property
    def visible_count(self) -> int:
        return len(self._popups)
