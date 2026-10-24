"""The event pipeline: session association -> dedup -> policy -> dispatch -> history.

``EventRouter.process`` is synchronous and safe to call from any single worker thread.
The desktop app runs it on the engine thread (see ``core/engine.py``); the hook's
headless fallback calls it directly. A failure in any stage is caught and logged; the
event is still recorded when possible, and nothing propagates to the caller.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from turnping.adapters.registry import AdapterRegistry, default_registry
from turnping.config import AppConfig
from turnping.contracts.events import AgentEvent, EventCategory
from turnping.core.deduplication import Deduplicator
from turnping.core.dispatcher import Dispatcher, format_notification
from turnping.core.policy import Action, Decision, PolicyContext, decide
from turnping.core.reminders import PendingReminder, ReminderScheduler
from turnping.core.session_manager import SessionManager, SessionState
from turnping.notifications.base import DeliveryResult, DeliveryStatus
from turnping.storage.repository import Repository

log = logging.getLogger(__name__)

C = EventCategory
PAUSED_KEY = "paused"


@dataclass(frozen=True)
class ProcessingResult:
    event: AgentEvent
    decision: Decision
    delivery: DeliveryResult | None
    session_state: SessionState | None
    duplicate: bool = False
    reminder_scheduled: bool = False
    reminder_resolved: bool = False
    persisted: bool = True

    @property
    def summary(self) -> str:
        delivery = f", delivery={self.delivery.status.value}" if self.delivery else ""
        return f"{self.event.category.value}: {self.decision.action.value} ({self.decision.reason}{delivery})"


def _is_activity(event: AgentEvent) -> bool:
    return event.category is C.SESSION_RESUMED and "activity" in event.metadata


class EventRouter:
    def __init__(
        self,
        repo: Repository,
        config_getter: Callable[[], AppConfig],
        dispatcher: Dispatcher,
        *,
        reminders: ReminderScheduler | None = None,
        registry: AdapterRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.config_getter = config_getter
        self.dispatcher = dispatcher
        self.reminders = reminders or ReminderScheduler()
        self.registry = registry or default_registry()
        self.sessions = SessionManager(repo)
        self.clock = clock
        self.listeners: list[Callable[[ProcessingResult], None]] = []

    # ----- state --------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.repo.get_state(PAUSED_KEY, "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.repo.set_state(PAUSED_KEY, "1" if paused else "0")
        if paused:
            self.reminders.cancel_all()

    def _supports_resolution(self, provider: str) -> bool:
        adapter = self.registry.get(provider)
        if adapter is None:
            adapter = self.registry.get("generic")
        return bool(adapter and adapter.capabilities.supports_escalation)

    # ----- pipeline -----------------------------------------------------------

    def process(self, event: AgentEvent) -> ProcessingResult:
        try:
            result = self._process(event)
        except Exception as exc:  # the pipeline must never take the app down
            log.exception(
                "failed to process %s event from %s", event.category.value, event.provider
            )
            result = ProcessingResult(
                event=event,
                decision=Decision(Action.SUPPRESS, f"internal error: {exc.__class__.__name__}"),
                delivery=None,
                session_state=None,
                persisted=False,
            )
        self._notify_listeners(result)
        return result

    def _process(self, event: AgentEvent) -> ProcessingResult:
        config = self.config_getter()
        now = self.clock()

        dedup = Deduplicator(self.repo, config.dedup.window_seconds)
        dup_reason = dedup.duplicate_reason(event, now)
        if dup_reason:
            log.info(
                "dropping duplicate %s from %s: %s", event.native_type, event.provider, dup_reason
            )
            return ProcessingResult(
                event=event,
                decision=Decision(Action.SUPPRESS, dup_reason),
                delivery=None,
                session_state=None,
                duplicate=True,
                persisted=False,
            )

        previous = self.sessions.previous(event)
        prev_state = SessionState(previous.state) if previous else None
        if _is_activity(event) and previous is not None and previous.last_seen > event.timestamp:
            # Activity hooks run asynchronously and can arrive after a later event (e.g.
            # a PostToolUse landing after Stop). A stale activity event must not
            # regress the session state or cancel a newer alert's reminder.
            decision = Decision(Action.RECORD, "stale activity event ignored")
            return ProcessingResult(event, decision, None, prev_state, persisted=False)
        state = self.sessions.apply(event, now, previous)

        resolved = False
        adapter = self.registry.get(event.provider) or self.registry.get("generic")
        resolution = adapter.capabilities.resolution_signals if adapter else frozenset()
        if event.category in resolution or event.category in (C.SESSION_ENDED, C.TASK_FAILED):
            resolved = self.reminders.resolve(event.session_key)
            if resolved:
                log.info("reminder for %s resolved by %s", event.session_key, event.native_type)

        ctx = PolicyContext(
            now_local=now.astimezone(),
            paused=self.paused,
            previous_state=prev_state,
            provider_supports_resolution=self._supports_resolution(event.provider),
        )
        decision = decide(event, config, ctx)

        delivery: DeliveryResult | None = None
        if decision.notify:
            delivery = self._deliver(event, decision, config)

        scheduled = False
        if decision.notify and event.category.needs_attention and ctx.provider_supports_resolution:
            scheduled = self.reminders.schedule(event, config.reminders, now)
            if scheduled:
                log.info("reminder scheduled for %s", event.session_key)

        persisted = not _is_activity(event)
        if persisted:
            self.repo.record_event(
                event,
                decision=decision.action.value,
                reason=decision.reason,
                priority=decision.priority.value,
                delivery_status=(
                    delivery.status.value if delivery else DeliveryStatus.NOT_SENT.value
                ),
                delivery_detail=(delivery.detail if delivery else None),
                received_at=now,
            )
        return ProcessingResult(
            event=event,
            decision=decision,
            delivery=delivery,
            session_state=state,
            reminder_scheduled=scheduled,
            reminder_resolved=resolved,
            persisted=persisted,
        )

    def _deliver(
        self,
        event: AgentEvent,
        decision: Decision,
        config: AppConfig,
        *,
        is_reminder: bool = False,
        reminder_number: int = 0,
    ) -> DeliveryResult:
        notification = format_notification(
            event,
            agent_name=self.registry.display_name(event.provider),
            priority=decision.priority,
            sound=decision.sound,
            show_project_path=config.privacy.show_project_path,
            is_reminder=is_reminder,
            reminder_number=reminder_number,
        )
        try:
            return self.dispatcher.dispatch(notification)
        except Exception as exc:
            log.exception("notification dispatch raised")
            return DeliveryResult(DeliveryStatus.FAILED, "dispatcher", f"error: {exc}")

    # ----- reminders ----------------------------------------------------------

    def fire_due_reminders(self) -> list[ProcessingResult]:
        config = self.config_getter()
        now = self.clock()
        results = []
        for pending in self.reminders.pop_due(config.reminders, now):
            log.info("reminder %d firing for %s", pending.fired, pending.session_key)
            results.append(self._fire_reminder(pending, config, now))
        return results

    def _fire_reminder(
        self, pending: PendingReminder, config: AppConfig, now: datetime
    ) -> ProcessingResult:
        source = pending.source_event
        reminder_event = source.model_copy(update={"event_id": uuid.uuid4().hex, "timestamp": now})
        ctx = PolicyContext(now_local=now.astimezone(), paused=self.paused, is_reminder=True)
        decision = decide(reminder_event, config, ctx)
        delivery = None
        if decision.notify:
            delivery = self._deliver(
                reminder_event, decision, config, is_reminder=True, reminder_number=pending.fired
            )
        else:
            self.reminders.resolve(pending.session_key)
            log.info("reminder for %s dropped: %s", pending.session_key, decision.reason)
        try:
            self.repo.record_event(
                reminder_event,
                decision=decision.action.value,
                reason=f"reminder {pending.fired}: {decision.reason}",
                priority=decision.priority.value,
                delivery_status=(
                    delivery.status.value if delivery else DeliveryStatus.NOT_SENT.value
                ),
                delivery_detail=(delivery.detail if delivery else None),
                is_reminder=True,
                received_at=now,
            )
        except Exception:
            log.exception("failed to record reminder")
        result = ProcessingResult(
            event=reminder_event, decision=decision, delivery=delivery, session_state=None
        )
        self._notify_listeners(result)
        return result

    # ----- user interaction ---------------------------------------------------

    def acknowledge(self, event_id: str, *, dismissed: bool) -> None:
        """The user clicked or dismissed a notification for ``event_id``."""
        config = self.config_getter()
        record = self.repo.get_event(event_id)
        if dismissed:
            self.repo.mark_dismissed(event_id)
        stop = record is not None and (not dismissed or config.reminders.stop_on_dismiss)
        if stop and record is not None and self.reminders.resolve(record.session_key):
            log.info(
                "reminder for %s stopped: notification %s",
                record.session_key,
                "dismissed" if dismissed else "opened",
            )

    def _notify_listeners(self, result: ProcessingResult) -> None:
        for listener in list(self.listeners):
            try:
                listener(result)
            except Exception:
                log.exception("event listener failed")
