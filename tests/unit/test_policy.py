from datetime import UTC, datetime

from turnping.config import AppConfig, ProviderConfig
from turnping.contracts.events import AgentEvent, EventCategory, Priority
from turnping.core.policy import Action, PolicyContext, decide
from turnping.core.session_manager import SessionState

NOON = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def ev(category=EventCategory.RESPONSE_STOPPED, provider="claude-code", **kw):
    return AgentEvent(provider=provider, native_type="X", category=category, **kw)


def ctx(**kw):
    kw.setdefault("now_local", NOON)
    return PolicyContext(**kw)


def test_default_response_stopped_notifies_with_sound():
    d = decide(ev(), AppConfig(), ctx())
    assert d.action is Action.NOTIFY and d.sound and d.priority is Priority.NORMAL


def test_permission_is_urgent_and_failure_high():
    cfg = AppConfig()
    assert decide(ev(EventCategory.PERMISSION_NEEDED), cfg, ctx()).priority is Priority.URGENT
    assert decide(ev(EventCategory.TASK_FAILED), cfg, ctx()).priority is Priority.HIGH


def test_state_only_categories_are_recorded_not_notified():
    for c in (EventCategory.SESSION_RESUMED, EventCategory.SESSION_ENDED, EventCategory.UNKNOWN):
        assert decide(ev(c), AppConfig(), ctx()).action is Action.RECORD


def test_global_disable_and_pause():
    cfg = AppConfig()
    cfg.general.enabled = False
    assert decide(ev(), cfg, ctx()).action is Action.SUPPRESS
    assert decide(ev(), AppConfig(), ctx(paused=True)).action is Action.SUPPRESS


def test_per_provider_disable():
    cfg = AppConfig(providers={"codex": ProviderConfig(enabled=False)})
    assert decide(ev(provider="codex"), cfg, ctx()).action is Action.SUPPRESS
    assert decide(ev(provider="claude-code"), cfg, ctx()).action is Action.NOTIFY


def test_per_event_disable():
    cfg = AppConfig()
    cfg.events["RESPONSE_STOPPED"].notify = False
    assert decide(ev(), cfg, ctx()).action is Action.RECORD


def test_sound_master_switch():
    cfg = AppConfig()
    cfg.sound.enabled = False
    assert decide(ev(), cfg, ctx()).sound is False


def test_quiet_hours_suppress_normal_but_show_urgent_silently():
    cfg = AppConfig()
    cfg.quiet_hours.enabled = True
    cfg.quiet_hours.start, cfg.quiet_hours.end = "11:00", "13:00"
    assert decide(ev(), cfg, ctx()).action is Action.SUPPRESS
    d = decide(ev(EventCategory.PERMISSION_NEEDED), cfg, ctx())
    assert d.action is Action.NOTIFY and d.sound is False
    cfg.quiet_hours.allow_urgent = False
    assert decide(ev(EventCategory.PERMISSION_NEEDED), cfg, ctx()).action is Action.SUPPRESS


def test_quiet_hours_use_local_time():
    cfg = AppConfig()
    cfg.quiet_hours.enabled = True
    cfg.quiet_hours.start, cfg.quiet_hours.end = "11:00", "13:00"
    outside = ctx(now_local=datetime(2026, 10, 24, 15, 0, tzinfo=UTC))
    assert decide(ev(), cfg, outside).action is Action.NOTIFY


def test_idle_prompt_suppressed_unless_enabled():
    idle = ev(EventCategory.INPUT_NEEDED, metadata={"reason": "idle"})
    assert decide(idle, AppConfig(), ctx()).action is Action.SUPPRESS
    cfg = AppConfig(providers={"claude-code": ProviderConfig(notify_idle_prompt=True)})
    assert decide(idle, cfg, ctx()).action is Action.NOTIFY


def test_coalesce_repeated_attention_without_resolution():
    perm = ev(EventCategory.PERMISSION_NEEDED)
    d = decide(perm, AppConfig(), ctx(previous_state=SessionState.NEEDS_PERMISSION))
    assert d.action is Action.SUPPRESS
    # After activity (state active) a new permission request alerts again.
    assert decide(perm, AppConfig(), ctx(previous_state=SessionState.ACTIVE)).notify
    # Providers without resolution signals cannot coalesce safely.
    assert decide(
        perm,
        AppConfig(),
        ctx(previous_state=SessionState.NEEDS_PERMISSION, provider_supports_resolution=False),
    ).notify


def test_reminders_bypass_coalescing():
    perm = ev(EventCategory.PERMISSION_NEEDED)
    assert decide(
        perm, AppConfig(), ctx(previous_state=SessionState.NEEDS_PERMISSION, is_reminder=True)
    ).notify
