"""Engine thread, spool and authenticated IPC (real named pipes, isolated per test)."""

import json
import threading
import time
from multiprocessing.connection import Client, answer_challenge, deliver_challenge

import pytest

from agentping.contracts.events import AgentEvent, EventCategory
from agentping.core.engine import Engine
from agentping.ingest.ipc import (
    MAX_MESSAGE_BYTES,
    AlreadyRunningError,
    IpcServer,
    is_app_running,
    load_or_create_key,
    pipe_address,
    send_message,
)
from agentping.ingest.spool import MAX_FILES, Spool
from agentping.security.paths import get_paths
from tests.conftest import Harness


def ev(**kw):
    kw.setdefault("provider", "claude-code")
    kw.setdefault("native_type", "Stop")
    kw.setdefault("category", EventCategory.RESPONSE_STOPPED)
    kw.setdefault("session_id", "s1")
    return AgentEvent(**kw)


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_engine_processes_events_off_thread_and_stops_cleanly():
    h = Harness()
    engine = Engine(h.router, tick_seconds=0.05)
    seen = []
    h.router.listeners.append(lambda r: seen.append(threading.current_thread().name))
    engine.start()
    assert engine.submit(ev())
    assert wait_for(lambda: engine.processed == 1)
    assert seen == ["agentping-engine"]
    engine.stop()
    assert not engine.running


def test_engine_queue_is_bounded():
    h = Harness()
    engine = Engine(h.router, max_queue=2)  # not started: queue fills
    assert engine.submit(ev()) and engine.submit(ev())
    assert not engine.submit(ev())
    assert engine.dropped == 1


def test_engine_survives_errors_in_calls():
    h = Harness()
    engine = Engine(h.router, tick_seconds=0.05)
    engine.start()
    engine.call(lambda: 1 / 0)
    engine.submit(ev())
    assert wait_for(lambda: engine.processed == 1)
    assert "ZeroDivisionError" in (engine.last_error or "")
    engine.stop()


def test_engine_stop_cancels_reminders():
    h = Harness()
    h.router.reminders.schedule(
        ev(category=EventCategory.PERMISSION_NEEDED), h.config.reminders, h.clock.now
    )
    engine = Engine(h.router, tick_seconds=0.05)
    engine.start()
    engine.stop()
    assert h.router.reminders.pending() == []


def test_spool_roundtrip_bounds_and_invalid_files(tmp_path):
    spool = Spool(tmp_path / "spool")
    e = ev()
    assert spool.put(e) is not None
    (tmp_path / "spool" / "0-bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "spool" / "1-evil.json").write_text(
        json.dumps({"provider": "x", "__class__": "os.system"}), encoding="utf-8"
    )
    drained = spool.drain()
    assert [d.event_id for d in drained] == [e.event_id]
    assert spool.count() == 0
    for _ in range(MAX_FILES + 5):
        spool.put(ev())
    assert spool.count() == MAX_FILES


def test_spool_discards_stale_events(tmp_path):
    spool = Spool(tmp_path / "spool")
    spool.put(ev(timestamp="2020-01-01T00:00:00Z"))
    assert spool.drain() == []


def test_engine_drains_spool():
    h = Harness()
    spool = Spool(get_paths().spool_dir)
    spool.put(ev())
    engine = Engine(h.router, spool=spool, tick_seconds=0.05, spool_interval=0.01)
    engine.start()
    assert wait_for(lambda: engine.processed == 1)
    engine.stop()


@pytest.fixture
def server():
    received = []

    def handler(message):
        received.append(message)
        return {"ok": True, "type": message.get("type")}

    srv = IpcServer(handler)
    srv.start()
    srv.received = received
    yield srv
    srv.stop()


def test_ipc_roundtrip(server):
    response = send_message({"type": "event", "event": ev().to_wire()})
    assert response == {"ok": True, "type": "event"}
    assert server.received[0]["v"] == 1


def test_ipc_single_instance(server):
    with pytest.raises(AlreadyRunningError):
        IpcServer(lambda m: {}).start()


def test_ipc_rejects_wrong_key(server):
    with pytest.raises(ConnectionError):
        send_message({"type": "command", "command": "ping"}, key=b"\x00" * 32)
    assert server.received == []


def test_ipc_rejects_oversized_message(server):
    with pytest.raises((ConnectionError, ValueError)):
        send_message({"type": "x", "blob": "a" * (MAX_MESSAGE_BYTES + 10)})
    # raw client bypassing the size check in send_message
    key = load_or_create_key()
    conn = Client(pipe_address(), family="AF_PIPE")
    answer_challenge(conn, key)
    deliver_challenge(conn, key)
    # The server drops the connection; depending on the Python version the client sees
    # that while still writing (BrokenPipeError) or when reading the reply (EOFError).
    with pytest.raises((EOFError, OSError)):
        conn.send_bytes(b"x" * (MAX_MESSAGE_BYTES + 1))
        conn.recv_bytes()
    conn.close()
    assert server.received == []


def test_ipc_rejects_bad_json_and_versions(server):
    key = load_or_create_key()
    for raw in (b"not json", json.dumps({"v": 99}).encode(), json.dumps([1, 2]).encode()):
        conn = Client(pipe_address(), family="AF_PIPE")
        answer_challenge(conn, key)
        deliver_challenge(conn, key)
        conn.send_bytes(raw)
        reply = json.loads(conn.recv_bytes())
        conn.close()
        assert reply["ok"] is False
    assert server.received == []


def test_ipc_unreachable_when_not_running():
    load_or_create_key()
    with pytest.raises(ConnectionError):
        send_message({"type": "command", "command": "ping"})
    assert not is_app_running()


def test_ipc_handler_exception_returns_error():
    srv = IpcServer(lambda m: 1 / 0)
    srv.start()
    try:
        assert send_message({"type": "x"}) == {"ok": False, "error": "ZeroDivisionError"}
    finally:
        srv.stop()


def test_pipe_names_isolated_per_data_dir(tmp_path, monkeypatch):
    a = pipe_address()
    monkeypatch.setenv("AGENTPING_HOME", str(tmp_path / "other"))
    assert pipe_address() != a
