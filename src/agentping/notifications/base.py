"""Notification backend interface (OS-specific code lives behind this)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from agentping.contracts.events import EventCategory, Priority


class DeliveryStatus(StrEnum):
    CONFIRMED = "confirmed"  # the OS reports the notification is displayed / in its center
    DISPATCHED = "dispatched"  # handed to the OS/UI; display could not be confirmed
    FAILED = "failed"  # the backend tried and the OS rejected it
    UNAVAILABLE = "unavailable"  # no usable backend (e.g. notifications turned off)
    NOT_SENT = "not_sent"  # policy decided not to notify

    @property
    def delivered(self) -> bool:
        return self in (DeliveryStatus.CONFIRMED, DeliveryStatus.DISPATCHED)


@dataclass(frozen=True)
class Notification:
    event_id: str
    title: str
    body: str
    attribution: str
    priority: Priority
    category: EventCategory
    sound: bool = False
    launch_uri: str | None = None
    is_reminder: bool = False
    session_key: str | None = None


@dataclass(frozen=True)
class BackendStatus:
    available: bool
    detail: str
    fix: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    backend: str
    detail: str = ""
    attempts: list[str] = field(default_factory=list)


class NotificationBackend(ABC):
    name: str = "backend"
    plays_own_sound: bool = False

    @abstractmethod
    def status(self) -> BackendStatus:
        """Cheap availability check; must not raise."""

    @abstractmethod
    def show(self, notification: Notification) -> DeliveryResult:
        """Display the notification; must not raise."""

    def withdraw(self, event_id: str) -> None:  # noqa: B027 - optional hook
        """Remove a shown notification if the platform supports it."""
