"""Duplicate suppression.

Three rules, from strongest to weakest evidence:

1. The same ``event_id`` was already processed (re-delivery of an identical event).
2. The same provider-supplied ``dedup_key`` was seen within 24 hours. Adapters only set
   this when the source exposes a per-turn identifier (e.g. Claude's ``prompt_id``).
3. Without a key, an event with the same session, native type and message inside a
   short window (default 10 s) is treated as a retry. Legitimate repeats that are further
   apart, in another session, or with different content are never suppressed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from turnping.contracts.events import AgentEvent
from turnping.storage.repository import Repository

KEY_HORIZON = timedelta(hours=24)


class Deduplicator:
    def __init__(self, repo: Repository, window_seconds: int = 10) -> None:
        self.repo = repo
        self.window = timedelta(seconds=window_seconds)

    def duplicate_reason(self, event: AgentEvent, now: datetime) -> str | None:
        if self.repo.event_exists(event.event_id):
            return "duplicate event id"
        if event.dedup_key:
            if self.repo.dedup_key_seen(event.dedup_key, now - KEY_HORIZON):
                return "duplicate delivery (same provider event key)"
            return None
        if self.window.total_seconds() > 0 and self.repo.similar_event_seen(
            event.session_key, event.native_type, event.message, now - self.window
        ):
            return f"repeated within {int(self.window.total_seconds())}s"
        return None
