from pathlib import Path

import pytest

from turnping.contracts.events import AgentEvent, EventCategory, HostWindow, Priority
from turnping.core.dispatcher import format_notification, open_uri
from turnping.core.navigation import navigate, parse_open_uri
from turnping.installation.validation import command_executable
from turnping.launcher import shell_command
from turnping.notifications.base import Notification
from turnping.notifications.windows_toast import build_toast_xml


def ev(**kw):
    kw.setdefault("provider", "claude-code")
    kw.setdefault("native_type", "Stop")
    kw.setdefault("category", EventCategory.RESPONSE_STOPPED)
    return AgentEvent(**kw)


def test_open_uri_roundtrip():
    e = ev()
    assert parse_open_uri(open_uri(e.event_id)) == e.event_id


@pytest.mark.parametrize(
    "uri",
    [
        "turnping://open?event=../../etc",
        "turnping://open?event=a&event=b",
        "turnping://delete?event=abcdefgh12",
        "https://open?event=abcdefgh12",
        "turnping://open?event=" + "a" * 300,
        "turnping://open",
        "",
    ],
)
def test_open_uri_rejects_malformed(uri):
    assert parse_open_uri(uri) is None


def test_notification_formatting():
    n = format_notification(
        ev(session_id="1234567890", project_path="C:/src/app", message="done"),
        agent_name="Claude Code",
        priority=Priority.NORMAL,
        sound=False,
    )
    assert n.title == "Claude Code finished responding"
    assert n.body == "done"
    assert n.attribution == "app \u00b7 session 12345678"
    r = format_notification(
        ev(category=EventCategory.PERMISSION_NEEDED),
        agent_name="Codex CLI",
        priority=Priority.URGENT,
        sound=True,
        is_reminder=True,
        reminder_number=2,
    )
    assert r.title == "Reminder: Codex CLI needs your permission"
    assert "reminder 2" in r.body
    assert "session id not provided" in r.attribution


def test_toast_xml_escapes_and_uses_protocol_activation():
    n = Notification(
        event_id="abc12345",
        title='<b>"x"</b> & y',
        body="a < b",
        attribution="p",
        priority=Priority.URGENT,
        category=EventCategory.PERMISSION_NEEDED,
        launch_uri="turnping://open?event=abc12345",
    )
    xml = build_toast_xml(n)
    assert "<b>" not in xml and "&lt;b&gt;" in xml and "&amp; y" in xml
    assert 'activationType="protocol"' in xml and 'scenario="reminder"' in xml
    assert '<audio silent="true"/>' in xml
    from xml.dom.minidom import parseString

    parseString(xml)  # well-formed


def test_toast_xml_normal_priority_no_reminder_scenario():
    n = Notification(
        event_id="abc12345",
        title="t",
        body="b",
        attribution="a",
        priority=Priority.NORMAL,
        category=EventCategory.RESPONSE_STOPPED,
        launch_uri=None,
    )
    xml = build_toast_xml(n)
    assert "scenario" not in xml and "activationType" not in xml


def test_shell_command_quoting():
    assert (
        shell_command(["C:\\py\\python.exe", "-I", "-m", "turnping", "hook", "x"])
        == "C:/py/python.exe -I -m turnping hook x"
    )
    assert (
        shell_command(["C:\\Program Files\\A\\a.exe", "hook"]) == '"C:/Program Files/A/a.exe" hook'
    )


@pytest.mark.parametrize(
    "command, exe",
    [
        ('"C:/Program Files/x.exe" -a', "C:/Program Files/x.exe"),
        ("C:/py/python.exe -I -m turnping", "C:/py/python.exe"),
        ("", None),
    ],
)
def test_command_executable(command, exe):
    assert command_executable(command) == exe


def test_navigation_without_host_reports_failure():
    result = navigate(None, "C:/nowhere")
    assert not result.ok and "no window" in result.detail


def test_navigation_refuses_stale_window(monkeypatch):
    monkeypatch.setattr("turnping.platform.windows._window_pid", lambda hwnd: 999)
    monkeypatch.setattr("turnping.platform.windows.IS_WINDOWS", True)
    result = navigate(HostWindow(hwnd=1, pid=5, process_name="code.exe"), None)
    assert not result.ok and "another app" in result.detail


def test_asset_icons_exist():
    assets = Path(__file__).parents[2] / "src" / "turnping" / "desktop" / "assets"
    assert (assets / "turnping.png").exists() and (assets / "turnping.ico").exists()
