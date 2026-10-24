"""Click-to-return: bring the user back to the session that raised an event.

Strategy, in order:
1. Focus the terminal/editor window captured by the hook (verified by pid so a reused
   window handle is never focused by mistake).
2. For VS Code-family editors, ask the editor to focus the window with the project
   folder open (``code <folder>``), a supported editor CLI mechanism.
3. Otherwise report failure so the caller shows the session details (project path,
   session id, terminal) with instructions for returning manually.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from turnping import URI_SCHEME
from turnping.contracts.events import HostWindow
from turnping.platform import windows as win
from turnping.storage.repository import Repository

EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
_VSCODE_FAMILY = {"vscode", "vscode-insiders"}


@dataclass(frozen=True)
class NavigationResult:
    ok: bool
    method: str
    detail: str


def parse_open_uri(uri: str) -> str | None:
    """Return the event id from ``turnping://open?event=<id>``; None if malformed."""
    if not isinstance(uri, str) or len(uri) > 300:
        return None
    parts = urlsplit(uri.strip())
    if parts.scheme.lower() != URI_SCHEME or parts.netloc.lower() != "open":
        return None
    values = parse_qs(parts.query).get("event", [])
    if len(values) != 1 or not EVENT_ID_RE.match(values[0]):
        return None
    return values[0]


def navigate(host: HostWindow | None, project_path: str | None) -> NavigationResult:
    if host is not None and host.hwnd:
        ok, detail = win.focus_window(host)
        if ok:
            return NavigationResult(True, "window", detail)
        window_detail = detail
    else:
        window_detail = "no window was recorded for this session"
    if host is not None and host.terminal in _VSCODE_FAMILY and project_path:
        ok, detail = win.open_in_vscode(project_path)
        if ok:
            return NavigationResult(True, "vscode", detail)
    return NavigationResult(False, "none", window_detail)


def navigate_to_event(repo: Repository, event_id: str) -> tuple[NavigationResult, str | None]:
    """Returns the result and the session key (for acknowledging reminders)."""
    record = repo.get_event(event_id)
    if record is None:
        return NavigationResult(False, "none", "this notification is no longer in history"), None
    session = repo.get_session(record.session_key)
    host = session.host if session else None
    return navigate(host, record.project_path), record.session_key
