"""Safe install/uninstall into agent configuration files (all in a fake user home)."""

import json
import os

import pytest

from turnping.installation.backups import list_backups
from turnping.installation.hooks import (
    ClaudeCodeInstaller,
    CodexInstaller,
    GeminiCliInstaller,
    IntegrationStatus,
)
from turnping.security.paths import get_paths

# Shape of a real user's existing settings: another tool's hooks on many events.
OTHER_TOOL = '"C:/Users/me/projects/Other/.venv/Scripts/python.exe" -I -m other.hooks.claude'
EXISTING = {
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": OTHER_TOOL, "timeout": 10}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": OTHER_TOOL}]}],
        "PreCompact": [{"hooks": [{"type": "command", "command": OTHER_TOOL}]}],
    },
    "statusLine": {"type": "command", "command": OTHER_TOOL},
    "theme": "dark",
    "model": "opus",
    "permissions": {"allow": ["Bash(npm test)"]},
}


def write_settings(path, data, *, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def claude(user_home):
    installer = ClaudeCodeInstaller()
    assert installer.config_path() == user_home / ".claude" / "settings.json"
    return installer


def test_install_preserves_unrelated_settings_and_hooks(claude):
    path = claude.config_path()
    write_settings(path, EXISTING)
    report = claude.install()
    assert report.ok and report.changed and report.backup and report.backup.exists()
    data = read(path)
    for key in ("statusLine", "theme", "model", "permissions"):
        assert data[key] == EXISTING[key]
    # other tool's handlers untouched and still first
    assert data["hooks"]["Stop"][0] == EXISTING["hooks"]["Stop"][0]
    assert data["hooks"]["PostToolUse"][0] == EXISTING["hooks"]["PostToolUse"][0]
    assert data["hooks"]["PreCompact"] == EXISTING["hooks"]["PreCompact"]
    assert claude.installed_events(data) == {s.event for s in claude.specs()}
    assert claude.strip_ours(data) == EXISTING
    assert report.ok  # regression: verification must not roll back a good install


def test_install_is_idempotent(claude):
    path = claude.config_path()
    write_settings(path, EXISTING)
    claude.install()
    once = path.read_text(encoding="utf-8")
    report = claude.install()
    assert report.ok and not report.changed
    assert path.read_text(encoding="utf-8") == once
    assert len(list_backups(get_paths().backup_dir, "claude-code")) == 1


def test_uninstall_restores_exact_original_structure(claude):
    path = claude.config_path()
    write_settings(path, EXISTING)
    claude.install()
    report = claude.uninstall()
    assert report.ok and report.changed
    assert read(path) == EXISTING
    again = claude.uninstall()
    assert again.ok and not again.changed


def test_install_into_missing_file_and_uninstall_leaves_valid_json(claude):
    path = claude.config_path()
    report = claude.install()
    assert report.ok and report.backup is None
    assert claude.installed_events(read(path))
    claude.uninstall()
    assert read(path) == {"hooks": {}}  # valid, empty; TurnPing entries gone


def test_preexisting_empty_hooks_object_is_kept(claude):
    path = claude.config_path()
    write_settings(path, {"hooks": {}, "theme": "light"})
    assert claude.install().ok
    assert claude.uninstall().ok
    assert read(path) == {"hooks": {}, "theme": "light"}


def test_handler_sharing_a_group_with_ours_is_preserved(claude):
    path = claude.config_path()
    claude.install()
    data = read(path)
    data["hooks"]["Stop"][-1]["hooks"].append({"type": "command", "command": "echo mine"})
    write_settings(path, data)
    claude.uninstall()
    assert read(path)["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo mine"}]}]


def test_refuses_invalid_json_and_leaves_file_untouched(claude):
    path = claude.config_path()
    raw = '{\n  // my comment\n  "theme": "dark",\n}\n'
    write_settings(path, None, raw=raw)
    report = claude.install()
    assert not report.ok and "not plain JSON" in report.messages[0]
    assert path.read_text(encoding="utf-8") == raw
    assert claude.status().status is IntegrationStatus.MISCONFIGURED


@pytest.mark.parametrize(
    "bad", [[1, 2], {"hooks": []}, {"hooks": {"Stop": {}}}, {"hooks": {"Stop": [5]}}]
)
def test_refuses_unexpected_shapes(claude, bad):
    path = claude.config_path()
    write_settings(path, bad)
    before = path.read_text(encoding="utf-8")
    assert not claude.install().ok
    assert path.read_text(encoding="utf-8") == before


def test_write_failure_rolls_back_from_backup(claude, monkeypatch):
    path = claude.config_path()
    write_settings(path, EXISTING)
    original = path.read_text(encoding="utf-8")

    def broken_write(self, p, root, original_text, mtime):
        p.write_text('{"truncated": ', encoding="utf-8")  # simulate a crash mid-write

    monkeypatch.setattr(ClaudeCodeInstaller, "_write", broken_write)
    report = claude.install()
    assert not report.ok and "restored" in " ".join(report.messages)
    assert path.read_text(encoding="utf-8") == original


def test_concurrent_modification_detected(claude, monkeypatch):
    path = claude.config_path()
    write_settings(path, EXISTING)
    real_read = ClaudeCodeInstaller._read

    def read_then_touch(self, p):
        result = real_read(self, p)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # another program saved it
        return result

    monkeypatch.setattr(ClaudeCodeInstaller, "_read", read_then_touch)
    report = claude.install()
    assert not report.ok
    monkeypatch.undo()
    assert claude.strip_ours(read(path)) == EXISTING


def test_status_lifecycle(claude, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert claude.status().status is IntegrationStatus.NOT_INSTALLED
    claude.install()
    assert claude.status().status is IntegrationStatus.INSTALLED_UNVERIFIED
    from datetime import UTC, datetime

    assert claude.status(datetime.now(UTC)).status is IntegrationStatus.OPERATIONAL
    # A hook command pointing to a program that no longer exists is misconfigured.
    data = read(claude.config_path())
    data["hooks"]["Stop"][-1]["hooks"][0]["command"] = (
        "C:/gone/python.exe -I -m turnping hook claude-code"
    )
    write_settings(claude.config_path(), data)
    assert claude.status().status is IntegrationStatus.MISCONFIGURED


def test_missing_events_reported(claude):
    claude.install()
    data = read(claude.config_path())
    del data["hooks"]["Notification"]
    write_settings(claude.config_path(), data)
    report = claude.status()
    assert report.status is IntegrationStatus.MISCONFIGURED and "Notification" in report.detail


def test_idle_option_changes_matcher(claude):
    claude.install({"notify_idle_prompt": True})
    matcher = read(claude.config_path())["hooks"]["Notification"][-1]["matcher"]
    assert "idle_prompt" in matcher
    claude.install({"notify_idle_prompt": False})
    matcher = read(claude.config_path())["hooks"]["Notification"][-1]["matcher"]
    assert "idle_prompt" not in matcher
    assert len(read(claude.config_path())["hooks"]["Notification"]) == 1


def test_claude_spec_never_installs_permission_request(claude):
    assert "PermissionRequest" not in {s.event for s in claude.specs()}
    sync = {s.event for s in claude.specs() if not s.handler_extra.get("async")}
    assert {"Stop", "Notification"} <= sync


def test_hook_command_is_marked_and_shell_safe(claude):
    cmd = claude.hook_command()
    assert "hook claude-code" in cmd and "\\" not in cmd
    assert claude.is_ours({"type": "command", "command": cmd})
    assert not claude.is_ours({"type": "command", "command": OTHER_TOOL})
    assert not CodexInstaller().is_ours({"type": "command", "command": cmd})


def test_codex_installer_uses_hooks_json_and_async_permission(user_home):
    codex = CodexInstaller()
    assert codex.config_path() == user_home / ".codex" / "hooks.json"
    write_settings(
        codex.config_path(),
        {
            "description": "mine",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 notify.py"}]}]},
        },
    )
    assert codex.install().ok
    data = read(codex.config_path())
    assert data["description"] == "mine"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "python3 notify.py"
    perm = data["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert perm["async"] is True  # background hooks cannot approve or deny
    codex.uninstall()
    assert read(codex.config_path())["hooks"] == {
        "Stop": [{"hooks": [{"type": "command", "command": "python3 notify.py"}]}]
    }


def test_gemini_installer_uses_ms_timeouts_and_preserves_settings(user_home):
    gemini = GeminiCliInstaller()
    write_settings(gemini.config_path(), {"theme": "GitHub", "hooks": {}})
    assert gemini.install().ok
    data = read(gemini.config_path())
    assert data["theme"] == "GitHub"
    handler = data["hooks"]["AfterAgent"][0]["hooks"][0]
    assert handler["timeout"] == 10000 and handler["name"] == "turnping-after-agent"
    assert "AfterTool" not in data["hooks"]  # no per-tool sync hook (latency)
    gemini.uninstall()
    assert read(gemini.config_path()) == {"theme": "GitHub", "hooks": {}}


def test_gemini_settings_with_comments_refused(user_home):
    gemini = GeminiCliInstaller()
    write_settings(gemini.config_path(), None, raw='{ /* c */ "theme": "x" }')
    assert not gemini.install().ok


def test_installers_do_not_touch_each_others_files(user_home):
    ClaudeCodeInstaller().install()
    assert not (user_home / ".codex").exists()
    assert not (user_home / ".gemini").exists()


def test_backups_pruned(claude):
    path = claude.config_path()
    for i in range(25):
        write_settings(path, {"theme": f"t{i}"})
        claude.install()
    assert len(list_backups(get_paths().backup_dir, "claude-code")) == 20
