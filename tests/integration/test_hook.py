"""The hook entry point as each agent invokes it (stdin payload, stdout contract)."""

import io
import json
import subprocess
import sys

import pytest

from tests.conftest import load_fixture
from turnping.config import AppConfig, save_config
from turnping.hook import MAX_STDIN_BYTES, parse_payload, run_hook
from turnping.ingest.ipc import IpcServer
from turnping.ingest.spool import Spool
from turnping.notifications.base import DeliveryResult, DeliveryStatus
from turnping.security.paths import get_paths
from turnping.storage.database import Database
from turnping.storage.repository import Repository


def stdin_for(payload) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def no_launch_config():
    cfg = AppConfig()
    cfg.general.launch_app_on_event = False
    save_config(cfg, get_paths().ensure().config_file)


@pytest.fixture
def app_server():
    received = []
    srv = IpcServer(lambda m: (received.append(m), {"ok": True})[1])
    srv.start()
    srv.received = received
    yield srv
    srv.stop()


def test_hook_forwards_normalized_event_over_ipc(app_server):
    response, how = run_hook(
        "claude-code", stdin=stdin_for(load_fixture("claude_code", "stop")), capture_host=False
    )
    assert how == "ipc"
    assert response.stdout == "" and response.exit_code == 0
    [message] = app_server.received
    event = message["event"]
    assert event["category"] == "RESPONSE_STOPPED"
    blob = json.dumps(message)
    assert (
        "refactoring" not in blob and "transcript" not in blob
    )  # raw payload never leaves the hook


def test_hook_headless_when_app_not_running_and_launch_disabled(monkeypatch):
    no_launch_config()
    # The OS toast is mocked: CI machines (and this dev box) may have notifications off.
    monkeypatch.setattr(
        "turnping.runtime.WindowsToastBackend.show",
        lambda self, n: DeliveryResult(DeliveryStatus.DISPATCHED, "mock", "mocked toast"),
    )
    _, how = run_hook(
        "claude-code",
        stdin=stdin_for(load_fixture("claude_code", "notification_permission")),
        capture_host=False,
    )
    assert how == "headless"
    repo = Repository(Database(get_paths().database))
    [record] = repo.list_events()
    assert record.category == "PERMISSION_NEEDED" and record.delivery_status == "dispatched"
    repo.db.close()


def test_hook_spools_and_launches_app_when_not_running(monkeypatch):
    launched = []
    monkeypatch.setattr(
        "turnping.hook._launch_app_with_args", lambda args: launched.append(args) or True
    )
    _, how = run_hook(
        "claude-code", stdin=stdin_for(load_fixture("claude_code", "stop")), capture_host=False
    )
    assert how == "spooled"
    assert launched == [["run", "--background"]]
    assert Spool(get_paths().spool_dir).count() == 1


def test_launch_lock_prevents_spawn_storm(monkeypatch):
    launched = []
    monkeypatch.setattr(
        "turnping.hook._launch_app_with_args", lambda args: launched.append(args) or True
    )
    for _ in range(3):
        run_hook(
            "claude-code",
            stdin=stdin_for(load_fixture("claude_code", "stop") | {"prompt_id": None}),
            capture_host=False,
        )
    assert len(launched) == 1
    assert Spool(get_paths().spool_dir).count() == 3


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1,2]", b"\xff\xfe garbage"])
def test_hook_malformed_input_is_recorded_and_harmless(raw):
    response, how = run_hook("claude-code", stdin=io.BytesIO(raw), capture_host=False)
    assert how == "invalid"
    assert response.exit_code == 0 and response.stdout == ""
    failures = get_paths().hook_failures_file.read_text(encoding="utf-8")
    assert "claude-code" in failures


def test_hook_failure_log_has_shape_not_values():
    payload = {"prompt": "my secret plan", "session_id": "x"}  # no hook_event_name
    run_hook("claude-code", stdin=stdin_for(payload), capture_host=False)
    text = get_paths().hook_failures_file.read_text(encoding="utf-8")
    assert "my secret plan" not in text and '"prompt": "str"' in text


def test_gemini_hook_always_prints_json_even_on_errors():
    response, _ = run_hook("gemini-cli", stdin=io.BytesIO(b"garbage"), capture_host=False)
    assert response.stdout == "{}"


def test_ignored_events_short_circuit():
    _, how = run_hook(
        "claude-code",
        stdin=stdin_for(load_fixture("claude_code", "subagent_stop")),
        capture_host=False,
    )
    assert how == "ignored"


def test_disabled_provider_skips_processing():
    cfg = AppConfig.model_validate({"providers": {"codex": {"enabled": False}}})
    save_config(cfg, get_paths().ensure().config_file)
    response, how = run_hook(
        "codex", stdin=stdin_for(load_fixture("codex", "stop")), capture_host=False
    )
    assert how == "provider-disabled" and response.stdout == "{}"


def test_unknown_provider():
    _, how = run_hook("nope", stdin=io.BytesIO(b"{}"), capture_host=False)
    assert how == "unknown-provider"


def test_codex_legacy_notify_from_argv(app_server):
    payload = json.dumps(load_fixture("codex", "legacy_notify"))
    _, how = run_hook("codex", stdin=None, argv_payload=payload, capture_host=False)
    assert how == "ipc"
    assert app_server.received[0]["event"]["session_id"] == "thr_456"


def test_oversized_payload_recovers_routing_fields():
    big = {
        "hook_event_name": "PostToolUse",
        "session_id": "big",
        "cwd": "C:/x",
        "tool_response": "y" * (MAX_STDIN_BYTES + 100),
    }
    data = parse_payload(json.dumps(big).encode())
    assert data == {"hook_event_name": "PostToolUse", "session_id": "big", "cwd": "C:/x"}


def test_hook_subprocess_end_to_end(app_server, isolated_home, user_home):
    """Run the hook exactly as Claude Code does: a child process with JSON on stdin.

    The child gets a minimal environment (no USERNAME/LOCALAPPDATA), like Gemini CLI's
    sanitized hook environment: it must still find the app's pipe (regression test).
    """
    no_launch_config()
    env = {
        "TURNPING_HOME": str(isolated_home),
        "TURNPING_USER_HOME": str(user_home),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-m", "turnping", "hook", "claude-code"],
        input=json.dumps(load_fixture("claude_code", "notification_permission")).encode(),
        capture_output=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == b""  # Claude Code: no stdout, so no decision is ever made
    assert (
        app_server.received and app_server.received[0]["event"]["category"] == "PERMISSION_NEEDED"
    )


def test_gemini_hook_subprocess_stdout_is_pure_json(app_server, isolated_home, user_home):
    no_launch_config()
    env = {
        "TURNPING_HOME": str(isolated_home),
        "TURNPING_USER_HOME": str(user_home),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-m", "turnping", "hook", "gemini-cli"],
        input=json.dumps(load_fixture("gemini_cli", "after_agent")).encode(),
        capture_output=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell API")
def test_local_appdata_resolved_without_environment():
    import os

    from turnping.security.paths import _windows_local_appdata, current_user

    assert _windows_local_appdata().lower() == os.environ["LOCALAPPDATA"].lower()
    assert current_user().lower() == os.environ["USERNAME"].lower()
