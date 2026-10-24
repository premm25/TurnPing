"""Chooses a backend and delivers notifications, with honest fallback reporting.

Backend selection (``general.notification_backend``):

* ``toast``: native Windows toasts only.
* ``popup``: AgentPing's own on-screen popup (requires the desktop app to be running).
* ``auto`` (default): toast when Windows notifications are enabled, otherwise popup,
  otherwise report UNAVAILABLE with the reason.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from agentping.notifications.base import (
    BackendStatus,
    DeliveryResult,
    DeliveryStatus,
    Notification,
    NotificationBackend,
)
from agentping.notifications.sound import SoundPlayer

log = logging.getLogger(__name__)


class NotificationManager:
    def __init__(
        self,
        backends: dict[str, NotificationBackend] | None = None,
        sound: SoundPlayer | None = None,
        mode_getter: Callable[[], str] = lambda: "auto",
    ) -> None:
        self._backends: dict[str, NotificationBackend] = dict(backends or {})
        self._lock = threading.Lock()
        self.sound = sound or SoundPlayer()
        self.mode_getter = mode_getter

    def add_backend(self, key: str, backend: NotificationBackend) -> None:
        with self._lock:
            self._backends[key] = backend

    def remove_backend(self, key: str) -> None:
        with self._lock:
            self._backends.pop(key, None)

    def backend(self, key: str) -> NotificationBackend | None:
        with self._lock:
            return self._backends.get(key)

    def statuses(self) -> dict[str, BackendStatus]:
        with self._lock:
            items = list(self._backends.items())
        return {key: b.status() for key, b in items}

    def _order(self) -> list[str]:
        mode = self.mode_getter()
        if mode == "toast":
            return ["toast"]
        if mode == "popup":
            return ["popup"]
        return ["toast", "popup"]

    def deliver(self, notification: Notification) -> DeliveryResult:
        attempts: list[str] = []
        last_failure: DeliveryResult | None = None
        for key in self._order():
            backend = self.backend(key)
            if backend is None:
                attempts.append(f"{key}: not available in this process")
                continue
            result = backend.show(notification)
            attempts.append(f"{backend.name}: {result.status.value} ({result.detail})")
            if result.status.delivered:
                if notification.sound and not backend.plays_own_sound:
                    self.sound.play(notification.priority)
                return DeliveryResult(result.status, result.backend, result.detail, attempts)
            last_failure = result
        if last_failure is not None and last_failure.status is DeliveryStatus.FAILED:
            status = DeliveryStatus.FAILED
        else:
            status = DeliveryStatus.UNAVAILABLE
        detail = "; ".join(attempts) or "no notification backend configured"
        log.warning("notification %s not delivered: %s", notification.event_id, detail)
        return DeliveryResult(
            status, last_failure.backend if last_failure else "none", detail, attempts
        )

    def withdraw(self, event_id: str) -> None:
        with self._lock:
            backends = list(self._backends.values())
        for b in backends:
            b.withdraw(event_id)
