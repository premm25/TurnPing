import sqlite3
from datetime import UTC, datetime, timedelta

from agentping.contracts.events import AgentEvent, EventCategory, HostWindow
from agentping.storage.database import Database
from agentping.storage.migrations import MIGRATIONS
from agentping.storage.repository import Repository

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def ev(**kw):
    kw.setdefault("provider", "claude-code")
    kw.setdefault("native_type", "Stop")
    kw.setdefault("category", EventCategory.RESPONSE_STOPPED)
    return AgentEvent(**kw)


def repo():
    return Repository(Database(":memory:"))


def test_schema_version_matches_migrations():
    db = Database(":memory:")
    assert db.query("PRAGMA user_version")[0][0] == len(MIGRATIONS)


def test_record_and_query_with_filters():
    r = repo()
    r.record_event(
        ev(message="hello world", project_path="C:/a"), decision="notified", received_at=T0
    )
    r.record_event(
        ev(provider="codex", category=EventCategory.PERMISSION_NEEDED),
        decision="notified",
        received_at=T0 + timedelta(seconds=1),
    )
    assert len(r.list_events()) == 2
    assert [e.provider for e in r.list_events(provider="codex")] == ["codex"]
    assert len(r.list_events(category="PERMISSION_NEEDED")) == 1
    assert len(r.list_events(search="hello")) == 1
    assert r.list_events()[0].provider == "codex"  # newest first


def test_search_wildcards_are_literal():
    r = repo()
    r.record_event(ev(message="100% done"), decision="notified")
    r.record_event(ev(message="1000 done"), decision="notified")
    assert len(r.list_events(search="100%")) == 1
    assert len(r.list_events(search="_")) == 0


def test_sql_injection_in_search_is_inert():
    r = repo()
    r.record_event(ev(message="x"), decision="notified")
    assert r.list_events(search="'; DROP TABLE events; --") == []
    assert r.count_events() == 1


def test_duplicate_event_id_ignored():
    r = repo()
    e = ev()
    r.record_event(e, decision="notified")
    r.record_event(e, decision="notified")
    assert r.count_events() == 1


def test_prune_by_age_and_count():
    r = repo()
    for i in range(10):
        r.record_event(ev(), decision="notified", received_at=T0 - timedelta(days=i * 10))
    removed = r.prune(retention_days=35, max_events=3, now=T0)
    assert removed == 7
    assert r.count_events() == 3


def test_sessions_upsert_preserves_path_and_host():
    r = repo()
    host = HostWindow(hwnd=5, pid=9, process_name="code.exe")
    r.upsert_session(ev(session_id="s", project_path="C:/p", host=host), "active", T0)
    r.upsert_session(ev(session_id="s"), "response_finished", T0 + timedelta(seconds=5))
    s = r.get_session("claude-code:s")
    assert s.state == "response_finished"
    assert s.project_path == "C:/p"
    assert s.host == host
    assert r.provider_last_seen("claude-code") == T0 + timedelta(seconds=5)


def test_clear_history():
    r = repo()
    r.record_event(ev(session_id="s"), decision="notified")
    r.upsert_session(ev(session_id="s"), "active", T0)
    assert r.clear_history() == 1
    assert r.list_sessions() == []


def test_app_state():
    r = repo()
    assert r.get_state("paused", "0") == "0"
    r.set_state("paused", "1")
    assert r.get_state("paused") == "1"


def test_corrupt_database_is_moved_aside_not_deleted(tmp_path):
    path = tmp_path / "agentping.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)
    db = Database(path)
    assert db.recovery_note and "moved" in db.recovery_note
    corrupt = list(tmp_path.glob("agentping.corrupt-*.db"))
    assert len(corrupt) == 1
    assert corrupt[0].read_bytes().startswith(b"this is definitely")
    Repository(db).record_event(ev(), decision="notified")  # fresh db works
    db.close()


def test_newer_schema_refused(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    try:
        Database(path)
    except RuntimeError as exc:
        assert "newer" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
