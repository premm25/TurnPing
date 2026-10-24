"""Session association: tracks only what events substantiate.

A session's state is derived from the last meaningful event. TurnPing does not claim
to know more than the integration exposes: e.g. after RESPONSE_STOPPED we only know the
agent stopped responding, not whether the user's project is "done".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from turnping.contracts.events import AgentEvent, EventCategory
from turnping.storage.repository import Repository, SessionRecord

C = EventCategory


class SessionState(StrEnum):
    ACTIVE = "active"
    RESPONSE_FINISHED = "response_finished"
    COMPLETED = "completed"
    NEEDS_PERMISSION = "needs_permission"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    ENDED = "ended"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "active": "Working",
            "response_finished": "Response finished",
            "completed": "Task completed",
            "needs_permission": "Needs permission",
            "needs_input": "Needs input",
            "failed": "Failure reported",
            "ended": "Ended",
            "unknown": "Unknown",
        }[self.value]


CATEGORY_TO_STATE: dict[EventCategory, SessionState] = {
    C.SESSION_RESUMED: SessionState.ACTIVE,
    C.RESPONSE_STOPPED: SessionState.RESPONSE_FINISHED,
    C.TASK_COMPLETED: SessionState.COMPLETED,
    C.PERMISSION_NEEDED: SessionState.NEEDS_PERMISSION,
    C.INPUT_NEEDED: SessionState.NEEDS_INPUT,
    C.TASK_FAILED: SessionState.FAILED,
    C.SESSION_ENDED: SessionState.ENDED,
}

ATTENTION_STATES = frozenset({SessionState.NEEDS_PERMISSION, SessionState.NEEDS_INPUT})


class SessionManager:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def previous(self, event: AgentEvent) -> SessionRecord | None:
        return self.repo.get_session(event.session_key)

    def apply(
        self, event: AgentEvent, now: datetime, previous: SessionRecord | None
    ) -> SessionState:
        state = CATEGORY_TO_STATE.get(event.category)
        if state is None:
            state = SessionState(previous.state) if previous else SessionState.UNKNOWN
        self.repo.upsert_session(event, state.value, now)
        return state
