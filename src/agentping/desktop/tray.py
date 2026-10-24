"""System tray icon and menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from agentping import APP_NAME
from agentping.desktop import theme


class Tray(QObject):
    open_page = Signal(str)  # "dashboard" | "history" | "settings"
    pause_toggled = Signal(bool)
    test_requested = Signal()
    integrations_requested = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.icon = QSystemTrayIcon()
        self.icon.setIcon(theme.app_icon())
        self.icon.setToolTip(f"{APP_NAME}: watching your coding agents")
        self.menu = QMenu()
        self._paused = False

        def action(text: str, slot, tip: str = "") -> QAction:
            a = QAction(text, self.menu)
            a.triggered.connect(slot)
            if tip:
                a.setToolTip(tip)
            self.menu.addAction(a)
            return a

        action("Open dashboard", lambda: self.open_page.emit("dashboard"))
        action("Notification history", lambda: self.open_page.emit("history"))
        action("Settings", lambda: self.open_page.emit("settings"))
        self.menu.addSeparator()
        self.pause_action = action("Pause notifications", self._toggle_pause)
        action("Send test notification", self.test_requested.emit)
        action("Check integrations", self.integrations_requested.emit)
        self.menu.addSeparator()
        action(
            f"Exit {APP_NAME}",
            self.quit_requested.emit,
            "Stop AgentPing completely (closing the window keeps it running in the tray)",
        )
        self.menu.setToolTipsVisible(True)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)

    def show(self) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.icon.show()
        return True

    def hide(self) -> None:
        self.icon.hide()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_page.emit("dashboard")

    def _toggle_pause(self) -> None:
        self.pause_toggled.emit(not self._paused)

    def set_state(self, *, paused: bool, attention: int) -> None:
        self._paused = paused
        self.pause_action.setText("Resume notifications" if paused else "Pause notifications")
        badge = "#F59E0B" if attention and not paused else None
        self.icon.setIcon(theme.app_icon(paused=paused, badge=badge))
        if paused:
            tip = f"{APP_NAME}: notifications paused"
        elif attention:
            tip = f"{APP_NAME}: {attention} session(s) waiting for you"
        else:
            tip = f"{APP_NAME}: watching your coding agents"
        self.icon.setToolTip(tip)
