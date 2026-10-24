"""Adapter -> router -> notification backend, with a mocked OS backend."""

from tests.conftest import FakeBackend, Harness, load_fixture
from turnping.adapters.claude_code import ClaudeCodeAdapter
from turnping.config import AppConfig
from turnping.contracts.events import EventCategory as C
from turnping.contracts.events import HostWindow
from turnping.core.policy import Action
from turnping.core.session_manager import SessionState
from turnping.notifications.base import DeliveryStatus
from turnping.notifications.manager import NotificationManager
from turnping.notifications.sound import SilentPlayer


def claude(name, **overrides):
    payload = load_fixture("claude_code", name) | overrides
    return ClaudeCodeAdapter().translate(payload)


def test_stop_event_flows_to_backend_and_history(harness):
    result = harness.process(claude("stop"))
    assert result.decision.action is Action.NOTIFY
    assert result.delivery.status is DeliveryStatus.CONFIRMED
    [shown] = harness.backend.shown
    assert shown.title == "Claude Code finished responding"
    assert "webapp" in shown.attribution and "abc123"[:8] in shown.attribution
    assert shown.launch_uri.startswith("turnping://open?event=")
    [record] = harness.repo.list_events()
    assert record.delivery_status == "confirmed" and record.category == "RESPONSE_STOPPED"
    assert harness.sound.played  # default rule plays a sound


def test_permission_alert_is_distinct_from_completion(harness):
    harness.process(claude("notification_permission"))
    [shown] = harness.backend.shown
    assert shown.title == "Claude Code needs your permission"
    assert shown.priority.value == "urgent"


def test_duplicate_delivery_suppressed(harness):
    first = harness.process(claude("stop"))
    second = harness.process(claude("stop"))
    assert first.delivery and second.duplicate
    assert len(harness.backend.shown) == 1
    assert harness.repo.count_events() == 1


def test_same_event_id_redelivered_is_suppressed(harness):
    event = claude("stop")
    harness.process(event)
    assert harness.process(event).duplicate


def test_keyless_retry_window(harness):
    # Without a provider identifier, identical events within the window are retries...
    harness.process(claude("notification_idle"))  # idle suppressed by policy but recorded
    again = harness.process(claude("notification_idle"))
    assert again.duplicate
    # ...but the same event later is legitimate.
    harness.clock.advance(60)
    assert not harness.process(claude("notification_idle")).duplicate


def test_legitimate_repeats_in_other_sessions_not_suppressed(harness):
    harness.process(claude("stop"))
    other = harness.process(claude("stop", session_id="other-session"))
    assert not other.duplicate
    assert len(harness.backend.shown) == 2


def test_concurrent_sessions_in_same_directory_tracked_separately(harness):
    harness.process(claude("notification_permission", session_id="A"))
    harness.process(claude("stop", session_id="B"))
    sessions = {s.session_id: s.state for s in harness.repo.list_sessions()}
    assert sessions == {"A": "needs_permission", "B": "response_finished"}


def test_activity_updates_state_without_history_noise(harness):
    harness.process(claude("notification_permission"))
    result = harness.process(claude("post_tool_use"))
    assert result.session_state is SessionState.ACTIVE
    assert result.reminder_resolved
    assert harness.repo.count_events() == 1  # activity is not stored as history


def test_stale_async_activity_does_not_regress_state(harness):
    stale = claude("post_tool_use")  # stamped now, but processed after a later Stop
    harness.process(claude("stop"))
    harness.clock.advance(5)
    result = harness.process(
        stale.model_copy(update={"timestamp": harness.clock.now.replace(year=2020)}), restamp=False
    )
    assert result.decision.reason == "stale activity event ignored"
    assert harness.repo.get_session("claude-code:abc123").state == "response_finished"


def test_reminder_escalation_and_resolution(harness):
    harness.config.reminders.initial_delay_seconds = 60
    harness.config.reminders.repeat_interval_seconds = 60
    harness.config.reminders.max_reminders = 2
    first = harness.process(claude("notification_permission"))
    assert first.reminder_scheduled
    harness.clock.advance(61)
    [rem] = harness.router.fire_due_reminders()
    assert rem.delivery.status.delivered
    assert harness.backend.shown[-1].title.startswith("Reminder:")
    assert harness.backend.shown[-1].is_reminder
    # The user approves; the tool runs -> PostToolUse resolves the reminder.
    harness.process(claude("post_tool_use"))
    harness.clock.advance(600)
    assert harness.router.fire_due_reminders() == []
    reminders = [r for r in harness.repo.list_events() if r.is_reminder]
    assert len(reminders) == 1


def test_reminders_respect_max_count(harness):
    harness.config.reminders.initial_delay_seconds = 30
    harness.config.reminders.repeat_interval_seconds = 30
    harness.config.reminders.max_reminders = 3
    harness.process(claude("notification_permission"))
    fired = 0
    for _ in range(20):
        harness.clock.advance(31)
        fired += len(harness.router.fire_due_reminders())
    assert fired == 3


def test_reminder_cancelled_by_pause_and_session_end(harness):
    harness.config.reminders.initial_delay_seconds = 30
    harness.process(claude("notification_permission"))
    harness.router.set_paused(True)
    harness.clock.advance(31)
    assert harness.router.fire_due_reminders() == []
    harness.router.set_paused(False)
    harness.process(claude("notification_permission", session_id="z"))
    harness.process(claude("session_end", session_id="z"))
    harness.clock.advance(31)
    assert harness.router.fire_due_reminders() == []


def test_acknowledge_stops_reminders(harness):
    harness.config.reminders.initial_delay_seconds = 30
    result = harness.process(claude("notification_permission"))
    harness.router.acknowledge(result.event.event_id, dismissed=True)
    harness.clock.advance(31)
    assert harness.router.fire_due_reminders() == []
    assert harness.repo.get_event(result.event.event_id).dismissed


def test_no_reminders_for_gemini():
    from turnping.adapters.gemini_cli import GeminiCliAdapter

    h = Harness()
    h.config.reminders.initial_delay_seconds = 30
    result = h.process(
        GeminiCliAdapter().translate(load_fixture("gemini_cli", "notification_permission"))
    )
    assert result.delivery and not result.reminder_scheduled


def test_paused_events_still_recorded(harness):
    harness.router.set_paused(True)
    result = harness.process(claude("stop"))
    assert result.decision.action is Action.SUPPRESS
    assert harness.backend.shown == []
    assert harness.repo.list_events()[0].decision == "suppressed"


def test_backend_failure_is_reported_not_raised():
    h = Harness(backend=FakeBackend(status=DeliveryStatus.FAILED))
    result = h.process(claude("stop"))
    assert result.delivery.status is DeliveryStatus.FAILED
    assert h.repo.list_events()[0].delivery_status == "failed"


def test_backend_exception_does_not_break_pipeline():
    class Exploding(FakeBackend):
        def show(self, notification):
            raise RuntimeError("boom")

    h = Harness(backend=Exploding())
    result = h.process(claude("stop"))
    assert result.delivery.status is DeliveryStatus.FAILED
    assert h.repo.count_events() == 1
    # the next event still works
    assert h.process(claude("stop", session_id="next")).persisted


def test_fallback_to_popup_when_toasts_unavailable():
    toast = FakeBackend("toast", available=False)
    popup = FakeBackend("popup", status=DeliveryStatus.DISPATCHED)
    manager = NotificationManager({"toast": toast, "popup": popup}, sound=SilentPlayer())
    h = Harness()
    h.router.dispatcher.manager = manager
    result = h.process(claude("stop"))
    assert result.delivery.status is DeliveryStatus.DISPATCHED
    assert len(popup.shown) == 1
    assert "toast" in result.delivery.attempts[0]


def test_toast_only_mode_reports_unavailable():
    toast = FakeBackend("toast", available=False)
    manager = NotificationManager(
        {"toast": toast, "popup": FakeBackend("popup")}, mode_getter=lambda: "toast"
    )
    h = Harness()
    h.router.dispatcher.manager = manager
    assert h.process(claude("stop")).delivery.status is DeliveryStatus.UNAVAILABLE


def test_no_backends_reports_unavailable():
    h = Harness()
    h.router.dispatcher.manager = NotificationManager({})
    assert h.process(claude("stop")).delivery.status is DeliveryStatus.UNAVAILABLE


def test_host_window_recorded_on_session(harness):
    event = claude("stop").model_copy(
        update={"host": HostWindow(hwnd=1, pid=2, process_name="code.exe")}
    )
    harness.process(event)
    assert harness.repo.get_session("claude-code:abc123").host.process_name == "code.exe"


def test_project_path_hidden_when_configured():
    cfg = AppConfig()
    cfg.privacy.show_project_path = False
    h = Harness(config=cfg)
    h.process(claude("stop"))
    assert "webapp" not in h.backend.shown[0].attribution


def test_listener_errors_do_not_break_processing(harness):
    harness.router.listeners.append(lambda r: 1 / 0)
    assert harness.process(claude("stop")).delivery is not None


def test_categories_that_only_record(harness):
    for name in ("session_start", "session_end"):
        harness.process(claude(name))
    assert harness.backend.shown == []
    assert {r.category for r in harness.repo.list_events()} == {
        C.SESSION_RESUMED.value,
        C.SESSION_ENDED.value,
    }
