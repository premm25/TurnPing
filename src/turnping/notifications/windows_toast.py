"""Native Windows toast notifications through WinRT.

Unpackaged desktop apps must register an AppUserModelID for their toasts to be shown
with their own name and icon. We register ``HKCU\\Software\\Classes\\AppUserModelId\\
TurnPing.Desktop`` (per-user, no admin rights). Clicking a toast uses *protocol
activation* (``turnping://open?event=<id>``), which Windows routes to TurnPing even
after the process that showed the toast has exited.

Delivery is reported honestly:
* the notifier's ``setting`` tells us if the user/policy disabled notifications;
* the toast's ``failed`` event tells us if Windows rejected it;
* the toast appearing in ``ToastNotificationManager.history`` confirms it reached the
  Action Center. Otherwise we report DISPATCHED (shown or not, Windows did not say).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

from turnping import APP_ID, APP_NAME
from turnping.contracts.events import Priority
from turnping.notifications.base import (
    BackendStatus,
    DeliveryResult,
    DeliveryStatus,
    Notification,
    NotificationBackend,
)

log = logging.getLogger(__name__)

_SETTING_TEXT = {
    0: ("enabled", None),
    1: (
        "disabled for TurnPing",
        "Open Settings > System > Notifications and turn TurnPing on.",
    ),
    2: (
        "turned off for your Windows account",
        "Open Settings > System > Notifications and turn on 'Notifications'.",
    ),
    3: ("disabled by group policy", "Ask your administrator; TurnPing will use its own popups."),
    4: ("disabled by the app manifest", None),
}

TOAST_GROUP = "turnping"


def register_app_id(icon_path: str | None = None) -> None:
    """Idempotently register the per-user AppUserModelID used for toasts."""
    if sys.platform != "win32":
        return
    import winreg

    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}")
    try:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        if icon_path:
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
        winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    finally:
        winreg.CloseKey(key)


def unregister_app_id() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}")
        return True
    except FileNotFoundError:
        return False


def app_id_registered() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.CloseKey(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}")
        )
        return True
    except FileNotFoundError:
        return False


def build_toast_xml(n: Notification) -> str:
    launch = quoteattr(n.launch_uri) if n.launch_uri else None
    activation = f' launch={launch} activationType="protocol"' if launch else ""
    scenario = ""
    actions = ""
    if n.priority is Priority.URGENT or (n.priority is Priority.HIGH and n.is_reminder):
        # "reminder" keeps the toast on screen until the user acts. Windows requires
        # at least one button for the scenario to take effect.
        scenario = ' scenario="reminder"'
    if launch:
        actions = (
            "<actions>"
            f'<action content="Open session" activationType="protocol" arguments={launch}/>'
            '<action content="Dismiss" activationType="system" arguments="dismiss"/>'
            "</actions>"
        )
    duration = ' duration="long"' if n.priority.rank >= Priority.HIGH.rank else ""
    # Sound is played by TurnPing's sound module so it behaves identically for every
    # backend and respects quiet hours; the toast itself stays silent.
    return (
        f"<toast{activation}{scenario}{duration}>"
        '<visual><binding template="ToastGeneric">'
        f'<text hint-maxLines="1">{escape(n.title)}</text>'
        f"<text>{escape(n.body)}</text>"
        f'<text placement="attribution">{escape(n.attribution)}</text>'
        "</binding></visual>"
        f"{actions}"
        '<audio silent="true"/>'
        "</toast>"
    )


class WindowsToastBackend(NotificationBackend):
    name = "windows-toast"

    def __init__(self, confirm_timeout: float = 0.6) -> None:
        self.confirm_timeout = confirm_timeout
        self._import_error: str | None = None
        self._notifier = None
        self._lock = threading.Lock()
        self._live: dict[str, object] = {}  # keep toast objects alive for their events
        self.on_dismissed: Callable[[str, str], None] | None = None  # (event_id, reason)

    def _winrt(self):
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager

        return XmlDocument, ToastNotification, ToastNotificationManager

    def _get_notifier(self):
        if self._notifier is None:
            _, _, manager = self._winrt()
            self._notifier = manager.create_toast_notifier_with_id(APP_ID)
        return self._notifier

    def status(self) -> BackendStatus:
        if sys.platform != "win32":
            return BackendStatus(False, "Windows toasts are only available on Windows")
        try:
            notifier = self._get_notifier()
            setting = int(notifier.setting)
        except Exception as exc:
            return BackendStatus(False, f"WinRT notifications unavailable: {exc}")
        text, fix = _SETTING_TEXT.get(setting, (f"unknown state {setting}", None))
        if setting != 0:
            return BackendStatus(False, f"Windows notifications are {text}", fix)
        if not app_id_registered():
            return BackendStatus(
                True, "enabled (app id not yet registered; registers on first use)"
            )
        return BackendStatus(True, "Windows notifications are enabled")

    def show(self, notification: Notification) -> DeliveryResult:
        st = self.status()
        if not st.available:
            return DeliveryResult(DeliveryStatus.UNAVAILABLE, self.name, st.detail)
        try:
            if not app_id_registered():
                register_app_id()
            xml_cls, toast_cls, manager = self._winrt()
            doc = xml_cls()
            doc.load_xml(build_toast_xml(notification))
            toast = toast_cls(doc)
            toast.tag = notification.event_id[:64]
            toast.group = TOAST_GROUP
            failed = threading.Event()
            error: list[str] = []

            def _on_failed(_sender, args) -> None:
                try:
                    error.append(f"HRESULT {args.error_code.value:#x}")
                except Exception:
                    error.append("unknown error")
                failed.set()

            def _on_dismissed(_sender, args) -> None:
                callback = self.on_dismissed
                reason = {0: "user", 1: "app", 2: "timeout"}.get(int(args.reason), "?")
                if callback is not None:
                    try:
                        callback(notification.event_id, reason)
                    except Exception:
                        log.exception("dismiss callback failed")

            toast.add_failed(_on_failed)
            toast.add_dismissed(_on_dismissed)
            with self._lock:
                self._live[notification.event_id] = toast
                if len(self._live) > 100:
                    self._live.pop(next(iter(self._live)))
            self._get_notifier().show(toast)
            if failed.wait(self.confirm_timeout):
                return DeliveryResult(
                    DeliveryStatus.FAILED, self.name, f"Windows rejected the toast ({error[0]})"
                )
            if self._in_history(manager, toast.tag):
                return DeliveryResult(DeliveryStatus.CONFIRMED, self.name, "in Action Center")
            return DeliveryResult(
                DeliveryStatus.DISPATCHED,
                self.name,
                "sent to Windows; display not confirmed (Focus/Do Not Disturb may hide it)",
            )
        except Exception as exc:
            log.warning("toast delivery failed: %s", exc)
            return DeliveryResult(DeliveryStatus.FAILED, self.name, f"toast error: {exc}")

    @staticmethod
    def _in_history(manager, tag: str) -> bool:
        try:
            history = manager.history.get_history_with_id(APP_ID)
            return any(item.tag == tag for item in history)
        except Exception:
            return False

    def withdraw(self, event_id: str) -> None:
        try:
            _, _, manager = self._winrt()
            manager.history.remove_grouped_tag_with_id(event_id[:64], TOAST_GROUP, APP_ID)
        except Exception:  # noqa: S110 - withdrawal is best effort
            pass
