"""Turns a decided event into a user-facing notification and hands it to the manager."""

from __future__ import annotations

from urllib.parse import quote

from agentping import URI_SCHEME
from agentping.contracts.events import AgentEvent, EventCategory, Priority
from agentping.notifications.base import DeliveryResult, Notification
from agentping.notifications.manager import NotificationManager

C = EventCategory

_HEADLINES = {
    C.RESPONSE_STOPPED: "{agent} finished responding",
    C.TASK_COMPLETED: "{agent} completed a task",
    C.INPUT_NEEDED: "{agent} needs your input",
    C.PERMISSION_NEEDED: "{agent} needs your permission",
    C.TASK_FAILED: "{agent} reported a failure",
    C.SESSION_RESUMED: "{agent} session active",
    C.SESSION_ENDED: "{agent} session ended",
    C.UNKNOWN: "{agent} event",
}


def open_uri(event_id: str) -> str:
    return f"{URI_SCHEME}://open?event={quote(event_id, safe='')}"


def format_notification(
    event: AgentEvent,
    *,
    agent_name: str,
    priority: Priority,
    sound: bool,
    show_project_path: bool = True,
    is_reminder: bool = False,
    reminder_number: int = 0,
) -> Notification:
    headline = _HEADLINES[event.category].format(agent=agent_name)
    if is_reminder:
        headline = f"Reminder: {headline}"
    project = event.project_name if show_project_path else None
    body = event.message or event.category.label
    if is_reminder and reminder_number:
        body = f"{body} (still waiting, reminder {reminder_number})"
    parts = [project or "unknown project"]
    if event.session_id:
        parts.append(f"session {event.session_id[:8]}")
    elif event.session_key_inferred:
        parts.append("session id not provided")
    return Notification(
        event_id=event.event_id,
        title=headline,
        body=body,
        attribution=" · ".join(parts),
        priority=priority,
        category=event.category,
        sound=sound,
        launch_uri=open_uri(event.event_id),
        is_reminder=is_reminder,
        session_key=event.session_key,
    )


class Dispatcher:
    def __init__(self, manager: NotificationManager) -> None:
        self.manager = manager

    def dispatch(self, notification: Notification) -> DeliveryResult:
        return self.manager.deliver(notification)
