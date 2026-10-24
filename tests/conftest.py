from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from turnping.config import AppConfig
from turnping.contracts.events import AgentEvent, EventCategory
from turnping.core.dispatcher import Dispatcher
from turnping.core.reminders import ReminderScheduler
from turnping.core.router import EventRouter
from turnping.notifications.base import (
    BackendStatus,
    DeliveryResult,
    DeliveryStatus,
    Notification,
    NotificationBackend,
)
from turnping.notifications.manager import NotificationManager
from turnping.notifications.sound import SilentPlayer
from turnping.storage.database import Database
from turnping.storage.repository import Repository

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(provider: str, name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own TurnPing data dir and fake user home."""
    home = tmp_path / "turnping-home"
    user = tmp_path / "user-home"
    user.mkdir()
    monkeypatch.setenv("TURNPING_HOME", str(home))
    monkeypatch.setenv("TURNPING_USER_HOME", str(user))
    return home


@pytest.fixture
def user_home(tmp_path: Path) -> Path:
    return tmp_path / "user-home"


class FakeBackend(NotificationBackend):
    """Records notifications instead of showing them (OS behaviour is mocked)."""

    def __init__(
        self,
        name: str = "fake",
        status: DeliveryStatus = DeliveryStatus.CONFIRMED,
        available: bool = True,
    ) -> None:
        self.name = name
        self.result_status = status
        self.available = available
        self.shown: list[Notification] = []

    def status(self) -> BackendStatus:
        return BackendStatus(self.available, "fake backend")

    def show(self, notification: Notification) -> DeliveryResult:
        if not self.available:
            return DeliveryResult(DeliveryStatus.UNAVAILABLE, self.name, "disabled")
        self.shown.append(notification)
        return DeliveryResult(self.result_status, self.name, "fake")


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Harness:
    def __init__(self, config: AppConfig | None = None, backend: FakeBackend | None = None) -> None:
        self.config = config or AppConfig()
        self.backend = backend or FakeBackend()
        self.sound = SilentPlayer()
        self.manager = NotificationManager({"toast": self.backend}, sound=self.sound)
        self.db = Database(":memory:")
        self.repo = Repository(self.db)
        self.clock = Clock()
        self.router = EventRouter(
            self.repo,
            lambda: self.config,
            Dispatcher(self.manager),
            reminders=ReminderScheduler(),
            clock=self.clock,
        )

    def event(self, **kw: Any) -> AgentEvent:
        kw.setdefault("provider", "claude-code")
        kw.setdefault("native_type", "Stop")
        kw.setdefault("category", EventCategory.RESPONSE_STOPPED)
        kw.setdefault("session_id", "s1")
        kw.setdefault("project_path", "C:/src/app")
        kw.setdefault("timestamp", self.clock.now)
        return AgentEvent(**kw)

    def process(self, event: AgentEvent, *, restamp: bool = True):
        """Process an event; by default stamp it with the fake clock's time."""
        if restamp:
            event = event.model_copy(update={"timestamp": self.clock.now})
        result = self.router.process(event)
        self.clock.advance(0.001)
        return result


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def clock() -> Clock:
    return Clock()
