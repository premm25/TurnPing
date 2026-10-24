"""CLI commands, run in-process against an isolated home. Registry writes are mocked."""

import json

import pytest

from agentping import __version__
from agentping.cli import main
from agentping.installation.hooks import ClaudeCodeInstaller
from agentping.notifications.base import DeliveryResult, DeliveryStatus
from agentping.security.paths import get_paths


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agentping.installation.system.register_system_integration",
        lambda: calls.append("register"),
    )
    monkeypatch.setattr(
        "agentping.installation.system.unregister_system_integration",
        lambda: calls.append("unregister") or ["mock"],
    )
    monkeypatch.setattr(
        "agentping.installation.system.set_start_with_windows",
        lambda enabled: calls.append(f"startup={enabled}"),
    )
    return calls


def test_help_and_version(capsys):
    assert main([]) == 0
    assert "agentping" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_doctor_json(capsys):
    main(["doctor", "--json"])
    checks = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in checks}
    assert {"Operating system", "Configuration", "Windows notifications", "Desktop app"} <= names
    assert all(c["level"] in ("ok", "info", "warn", "error") for c in checks)


def test_integrations_lists_all(capsys):
    assert main(["integrations", "--json"]) == 0
    providers = {r["provider"] for r in json.loads(capsys.readouterr().out)}
    assert providers == {"claude-code", "codex", "gemini-cli"}


def test_install_and_uninstall_named_provider(capsys, user_home, no_registry):
    assert main(["install", "claude-code"]) == 0
    assert ClaudeCodeInstaller().installed_events(
        json.loads(ClaudeCodeInstaller().config_path().read_text())
    )
    assert "register" in no_registry
    assert main(["uninstall", "claude-code"]) == 0
    data = json.loads(ClaudeCodeInstaller().config_path().read_text())
    assert not ClaudeCodeInstaller().installed_events(data)


def test_install_unknown_provider(capsys):
    assert main(["install", "cursor"]) == 2


def test_install_nothing_detected(capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    main(["install"])
    assert "No supported coding agent was detected" in capsys.readouterr().out


def test_uninstall_all_keeps_data_unless_purged(capsys, no_registry):
    get_paths().ensure()
    (get_paths().home / "marker").write_text("x")
    assert main(["uninstall"]) == 0
    assert (get_paths().home / "marker").exists()
    assert "unregister" in no_registry
    assert main(["uninstall", "--purge-data"]) == 0
    assert not get_paths().home.exists()


def test_purge_preserves_settings_backups(capsys, user_home):
    path = ClaudeCodeInstaller().config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"theme": "dark"}')
    main(["install", "claude-code"])
    main(["uninstall", "--purge-data"])
    kept = get_paths().home.parent / "AgentPing-settings-backups"
    assert list(kept.rglob("*.bak"))


def test_send_generic_event_and_history(capsys, monkeypatch):
    monkeypatch.setattr(
        "agentping.hook._launch_app_with_args", lambda args: False
    )  # force headless
    monkeypatch.setattr(
        "agentping.runtime.WindowsToastBackend.show",
        lambda self, n: DeliveryResult(DeliveryStatus.DISPATCHED, "mock"),
    )
    assert (
        main(
            [
                "send",
                "--provider",
                "my-build",
                "--category",
                "TASK_COMPLETED",
                "--message",
                "Build done",
                "--project",
                "C:/src/app",
            ]
        )
        == 0
    )
    assert "headless" in capsys.readouterr().out
    assert main(["history", "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["provider"] == "my-build" and row["message"] == "Build done"
    assert main(["history", "--provider", "nobody"]) == 0
    assert "No matching events" in capsys.readouterr().out


def test_send_rejects_reserved_provider(capsys):
    assert main(["send", "--provider", "claude-code", "--category", "TASK_COMPLETED"]) == 2


def test_test_notification_reports_unavailable_honestly(capsys, monkeypatch):
    from agentping.notifications.base import BackendStatus

    monkeypatch.setattr(
        "agentping.runtime.WindowsToastBackend.status",
        lambda self: BackendStatus(False, "Windows notifications are turned off"),
    )
    assert main(["test-notification"]) == 1
    out = capsys.readouterr().out
    assert "unavailable" in out and "No notification was displayed" in out


def test_pause_resume_without_app(capsys):
    from agentping.runtime import build_runtime

    assert main(["pause"]) == 0
    rt = build_runtime(toast=False)
    assert rt.router.paused
    rt.close()
    assert main(["resume"]) == 0
    rt = build_runtime(toast=False)
    assert not rt.router.paused
    rt.close()


def test_open_uri_ignores_malformed(capsys):
    assert main(["open-uri", "agentping://evil?event=x"]) == 2


def test_quit_when_not_running(capsys):
    assert main(["quit"]) == 0
    assert "not running" in capsys.readouterr().out


def test_install_no_hooks_only_registers(capsys, user_home, no_registry):
    assert main(["install", "--no-hooks", "--startup"]) == 0
    assert not (user_home / ".claude").exists()
    assert no_registry == ["register", "startup=True"]
