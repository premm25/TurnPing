from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agentping.contracts.events import AgentEvent, EventCategory, HostWindow, Priority


def make(**kw):
    base = {"provider": "claude-code", "native_type": "Stop", "category": "RESPONSE_STOPPED"}
    base.update(kw)
    return AgentEvent(**base)


def test_minimal_event_gets_defaults():
    e = make()
    assert e.schema_version == 1
    assert len(e.event_id) == 32
    assert e.timestamp.tzinfo is not None
    assert e.category is EventCategory.RESPONSE_STOPPED


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-24T10:00:00Z", datetime(2026, 10, 24, 10, 0, tzinfo=UTC)),
        ("2026-10-24T12:00:00+02:00", datetime(2026, 10, 24, 10, 0, tzinfo=UTC)),
        ("2026-10-24T10:00:00", datetime(2026, 10, 24, 10, 0, tzinfo=UTC)),  # naive -> UTC
        (1793000000, datetime.fromtimestamp(1793000000, UTC)),
        (1793000000000, datetime.fromtimestamp(1793000000, UTC)),  # milliseconds
    ],
)
def test_timestamps_are_normalized_to_aware_utc(value, expected):
    e = make(timestamp=value)
    assert e.timestamp == expected
    assert e.timestamp.utcoffset() == timedelta(0)


def test_non_utc_aware_datetime_converted():
    tz = timezone(timedelta(hours=5, minutes=30))
    e = make(timestamp=datetime(2026, 1, 1, 5, 30, tzinfo=tz))
    assert e.timestamp == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45T99:00:00"])
def test_invalid_timestamp_rejected(bad):
    with pytest.raises(ValidationError):
        make(timestamp=bad)


def test_missing_timestamp_defaults_to_now():
    assert abs((make(timestamp=None).timestamp - datetime.now(UTC)).total_seconds()) < 5


@pytest.mark.parametrize("provider", ["Claude Code", "../etc", "", "a" * 41, "-x", "x;rm"])
def test_invalid_provider_ids_rejected(provider):
    with pytest.raises(ValidationError):
        make(provider=provider)


def test_provider_lowercased():
    assert make(provider="Codex").provider == "codex"


def test_unknown_category_rejected():
    with pytest.raises(ValidationError):
        make(category="EXPLODED")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        make(prompt="leak me")


def test_native_type_character_whitelist():
    with pytest.raises(ValidationError):
        make(native_type="Stop<script>")


def test_message_sanitized_truncated_and_redacted():
    e = make(message="line1\nline2\x07 token=supersecretvalue " + "x" * 400)
    assert "\n" not in e.message and "\x07" not in e.message
    assert "supersecretvalue" not in e.message
    assert len(e.message) <= 240


def test_metadata_flattened_and_limited():
    meta = {f"k{i}": i for i in range(30)}
    meta["nested"] = {"a": 1}
    meta["bad key!"] = "x"
    e = make(metadata=meta)
    assert len(e.metadata) <= 16
    assert "nested" not in e.metadata
    assert "bad key!" not in e.metadata


def test_session_key_uses_session_id_then_path():
    assert make(session_id="abc").session_key == "claude-code:abc"
    a = make(project_path="C:\\Src\\App")
    b = make(project_path="c:/src/app")
    assert a.session_key == b.session_key
    assert a.session_key_inferred
    assert make().session_key == "claude-code:unknown"


def test_concurrent_sessions_same_dir_stay_separate():
    a = make(session_id="one", project_path="C:/src/app")
    b = make(session_id="two", project_path="C:/src/app")
    assert a.session_key != b.session_key


def test_wire_roundtrip():
    e = make(
        session_id="s", metadata={"x": 1}, host=HostWindow(hwnd=1, pid=2, process_name="code.exe")
    )
    assert AgentEvent.from_wire(e.to_wire()) == e


def test_events_are_immutable():
    e = make()
    with pytest.raises(ValidationError):
        e.provider = "codex"  # type: ignore[misc]


def test_project_name():
    assert make(project_path="C:\\src\\webapp\\").project_name == "webapp"


def test_priority_rank_order():
    assert Priority.LOW.rank < Priority.NORMAL.rank < Priority.HIGH.rank < Priority.URGENT.rank


def test_needs_attention():
    assert EventCategory.PERMISSION_NEEDED.needs_attention
    assert EventCategory.INPUT_NEEDED.needs_attention
    assert not EventCategory.RESPONSE_STOPPED.needs_attention
