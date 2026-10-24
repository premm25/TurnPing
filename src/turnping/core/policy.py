"""Notification policy: decides whether and how loudly an event is surfaced.

Pure function of (event, config, context). No I/O, so every rule is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from turnping.config import AppConfig
from turnping.contracts.events import AgentEvent, Priority
from turnping.core.session_manager import ATTENTION_STATES, CATEGORY_TO_STATE, SessionState


class Action(StrEnum):
    NOTIFY = "notified"
    SUPPRESS = "suppressed"
    RECORD = "recorded"  # kept in history/session state only, by design


@dataclass(frozen=True)
class PolicyContext:
    now_local: datetime
    paused: bool = False
    previous_state: SessionState | None = None
    provider_supports_resolution: bool = True
    is_reminder: bool = False


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    priority: Priority = Priority.NORMAL
    sound: bool = False

    @property
    def notify(self) -> bool:
        return self.action is Action.NOTIFY


def decide(event: AgentEvent, config: AppConfig, ctx: PolicyContext) -> Decision:
    rule = config.rule(event.category)
    provider = config.provider(event.provider)
    priority = rule.priority

    if not provider.enabled:
        return Decision(Action.SUPPRESS, f"{event.provider} notifications are disabled", priority)
    if not rule.notify:
        return Decision(Action.RECORD, f"{event.category.value} is not set to notify", priority)
    if not config.general.enabled:
        return Decision(Action.SUPPRESS, "notifications are turned off", priority)
    if ctx.paused:
        return Decision(Action.SUPPRESS, "notifications are paused", priority)
    if (
        event.provider == "claude-code"
        and event.metadata.get("reason") == "idle"
        and not provider.notify_idle_prompt
    ):
        return Decision(Action.SUPPRESS, "idle reminders from Claude Code are off", priority)

    # Coalesce: the session is already waiting on the same thing and nothing has
    # happened since (e.g. Claude's PermissionRequest followed by permission_prompt).
    target_state = CATEGORY_TO_STATE.get(event.category)
    if (
        not ctx.is_reminder
        and ctx.provider_supports_resolution
        and target_state in ATTENTION_STATES
        and ctx.previous_state == target_state
    ):
        return Decision(Action.SUPPRESS, "already alerted for this pending request", priority)

    sound = config.sound.enabled and rule.sound
    quiet = config.quiet_hours
    if quiet.enabled and quiet.contains(ctx.now_local.time().replace(tzinfo=None)):
        if quiet.allow_urgent and priority.rank >= Priority.HIGH.rank:
            return Decision(Action.NOTIFY, "quiet hours: shown silently", priority, sound=False)
        return Decision(Action.SUPPRESS, "quiet hours", priority)

    return Decision(Action.NOTIFY, "matched notification rule", priority, sound=sound)
