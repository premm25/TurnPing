"""Reminder escalation for events that block the agent on the human.

Invariants:
* At most one pending reminder per session (a newer alert replaces the older one).
* A reminder fires at most ``max_reminders`` times, then is dropped. No infinite loops.
* Any resolution signal for the session (activity, a response finishing, the session
  ending, an explicit click/dismiss) cancels it.
* Reminders are only scheduled for providers whose integration exposes a resolution
  signal; otherwise TurnPing could not tell "still blocked" from "already answered".
* State is in-memory: exiting the app cancels everything (no stale reminders on restart).
* TurnPing never acts on the agent's request; a reminder is only another notification.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from turnping.config import RemindersConfig
from turnping.contracts.events import AgentEvent


@dataclass(frozen=True)
class PendingReminder:
    session_key: str
    source_event: AgentEvent
    due_at: datetime
    fired: int = 0


class ReminderScheduler:
    def __init__(self) -> None:
        self._pending: dict[str, PendingReminder] = {}
        self._lock = threading.Lock()

    def schedule(self, event: AgentEvent, config: RemindersConfig, now: datetime) -> bool:
        if not config.enabled or config.max_reminders <= 0:
            return False
        if event.category not in config.categories:
            return False
        reminder = PendingReminder(
            session_key=event.session_key,
            source_event=event,
            due_at=now + timedelta(seconds=config.initial_delay_seconds),
        )
        with self._lock:
            self._pending[event.session_key] = reminder
        return True

    def resolve(self, session_key: str) -> bool:
        with self._lock:
            return self._pending.pop(session_key, None) is not None

    def resolve_event(self, event_id: str) -> bool:
        with self._lock:
            for key, rem in list(self._pending.items()):
                if rem.source_event.event_id == event_id:
                    del self._pending[key]
                    return True
        return False

    def pop_due(self, config: RemindersConfig, now: datetime) -> list[PendingReminder]:
        """Return reminders that should fire now and reschedule or drop them."""
        due: list[PendingReminder] = []
        with self._lock:
            if not config.enabled:
                self._pending.clear()
                return due
            for key, rem in list(self._pending.items()):
                if rem.due_at > now:
                    continue
                fired = rem.fired + 1
                due.append(replace(rem, fired=fired))
                if fired >= config.max_reminders:
                    del self._pending[key]
                else:
                    self._pending[key] = replace(
                        rem,
                        fired=fired,
                        due_at=now + timedelta(seconds=config.repeat_interval_seconds),
                    )
        return due

    def pending(self) -> list[PendingReminder]:
        with self._lock:
            return list(self._pending.values())

    def cancel_all(self) -> int:
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            return count
