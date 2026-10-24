"""Per-user OS registration: toast app id, ``turnping://`` handler, sign-in startup.

All registrations live under ``HKEY_CURRENT_USER`` (no administrator rights) and are
removed by ``turnping uninstall``.
"""

from __future__ import annotations

from importlib import resources

from turnping import URI_SCHEME
from turnping.launcher import cli_command, gui_command
from turnping.notifications import windows_toast
from turnping.platform import windows as win


def icon_path() -> str | None:
    try:
        path = resources.files("turnping") / "desktop" / "assets" / "turnping.png"
        with resources.as_file(path) as p:
            return str(p) if p.exists() else None
    except (FileNotFoundError, ModuleNotFoundError):
        return None


def uri_handler_command() -> list[str]:
    return [*cli_command(), "open-uri"]


def startup_command() -> list[str]:
    return [*gui_command(), "run", "--background"]


def register_system_integration() -> None:
    """Idempotent; called at every app start so moved installs self-heal."""
    windows_toast.register_app_id(icon_path())
    win.register_uri_scheme(URI_SCHEME, uri_handler_command())
    if win.get_startup() is not None:
        win.set_startup(startup_command())  # keep the path current if enabled


def unregister_system_integration() -> list[str]:
    removed = []
    if windows_toast.unregister_app_id():
        removed.append("toast app id")
    if win.unregister_uri_scheme(URI_SCHEME):
        removed.append(f"{URI_SCHEME}:// handler")
    if win.get_startup() is not None:
        win.set_startup(None)
        removed.append("sign-in startup entry")
    return removed


def set_start_with_windows(enabled: bool) -> None:
    win.set_startup(startup_command() if enabled else None)


def start_with_windows_enabled() -> bool:
    return win.get_startup() is not None
