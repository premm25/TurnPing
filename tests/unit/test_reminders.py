from datetime import UTC, datetime, timedelta

from turnping.config import RemindersConfig
from turnping.contracts.events import AgentEvent, EventCategory
from turnping.core.reminders import ReminderScheduler

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def perm(session="s1"):
    return AgentEvent(
        provider="claude-code",
        native_type="Notification:permission_prompt",
        category=EventCategory.PERMISSION_NEEDED,
        session_id=session,
    )


def cfg(**kw):
    kw.setdefault("initial_delay_seconds", 60)
    kw.setdefault("repeat_interval_seconds", 120)
    kw.setdefault("max_reminders", 2)
    return RemindersConfig(**kw)


def test_fires_after_delay_then_repeats_then_stops():
    s, c = ReminderScheduler(), cfg()
    assert s.schedule(perm(), c, T0)
    assert s.pop_due(c, T0 + timedelta(seconds=59)) == []
    first = s.pop_due(c, T0 + timedelta(seconds=60))
    assert [r.fired for r in first] == [1]
    assert s.pop_due(c, T0 + timedelta(seconds=150)) == []
    second = s.pop_due(c, T0 + timedelta(seconds=180))
    assert [r.fired for r in second] == [2]
    # max reached: never fires again, no infinite loop
    assert s.pop_due(c, T0 + timedelta(days=1)) == []
    assert s.pending() == []


def test_resolution_cancels():
    s, c = ReminderScheduler(), cfg()
    s.schedule(perm(), c, T0)
    assert s.resolve("claude-code:s1")
    assert s.pop_due(c, T0 + timedelta(hours=1)) == []
    assert not s.resolve("claude-code:s1")


def test_one_reminder_per_session_no_duplicates():
    s, c = ReminderScheduler(), cfg()
    s.schedule(perm(), c, T0)
    s.schedule(perm(), c, T0 + timedelta(seconds=30))
    assert len(s.pending()) == 1
    assert len(s.pop_due(c, T0 + timedelta(seconds=95))) == 1


def test_sessions_are_independent():
    s, c = ReminderScheduler(), cfg()
    s.schedule(perm("a"), c, T0)
    s.schedule(perm("b"), c, T0)
    s.resolve("claude-code:a")
    due = s.pop_due(c, T0 + timedelta(seconds=60))
    assert [r.session_key for r in due] == ["claude-code:b"]


def test_disabled_or_zero_max_never_schedules():
    s = ReminderScheduler()
    assert not s.schedule(perm(), cfg(enabled=False), T0)
    assert not s.schedule(perm(), cfg(max_reminders=0), T0)
    assert s.pending() == []


def test_disabling_at_runtime_clears_pending():
    s = ReminderScheduler()
    s.schedule(perm(), cfg(), T0)
    assert s.pop_due(cfg(enabled=False), T0 + timedelta(hours=1)) == []
    assert s.pending() == []


def test_non_configured_category_not_scheduled():
    s = ReminderScheduler()
    c = cfg(categories=[EventCategory.INPUT_NEEDED])
    assert not s.schedule(perm(), c, T0)


def test_cancel_all_and_resolve_event():
    s, c = ReminderScheduler(), cfg()
    e = perm()
    s.schedule(e, c, T0)
    assert s.resolve_event(e.event_id)
    s.schedule(perm("x"), c, T0)
    assert s.cancel_all() == 1
