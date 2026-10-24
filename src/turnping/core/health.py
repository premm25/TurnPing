"""Diagnostics shared by ``turnping doctor`` and the dashboard.

Every check reports what it actually observed; nothing is assumed healthy.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from turnping import URI_SCHEME, __version__
from turnping.config import load_config
from turnping.installation.hooks import INSTALLERS, IntegrationStatus, StatusReport
from turnping.notifications.windows_toast import WindowsToastBackend, app_id_registered
from turnping.platform import windows as win
from turnping.security.paths import AppPaths, get_paths
from turnping.storage.repository import Repository


class Level(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str
    fix: str | None = None


def integration_statuses(repo: Repository | None) -> list[StatusReport]:
    config = load_config().config
    reports = []
    for pid, installer in INSTALLERS.items():
        last = repo.provider_last_seen(pid) if repo is not None else None
        options = {"notify_idle_prompt": config.provider(pid).notify_idle_prompt}
        reports.append(installer.status(last, options))
    return reports


def recent_hook_failures(paths: AppPaths, limit: int = 5) -> list[dict]:
    path = paths.hook_failures_file
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def run_checks(
    paths: AppPaths | None = None,
    repo: Repository | None = None,
    *,
    app_running: bool | None = None,
    toast_backend: WindowsToastBackend | None = None,
) -> list[Check]:
    paths = paths or get_paths()
    checks: list[Check] = []

    if sys.platform == "win32":
        checks.append(Check("Operating system", Level.OK, win.windows_version()))
    else:
        checks.append(
            Check(
                "Operating system",
                Level.WARN,
                f"{sys.platform}: only Windows is supported in v{__version__}",
            )
        )
    checks.append(
        Check("Runtime", Level.OK, f"TurnPing {__version__}, Python {sys.version.split()[0]}")
    )

    cfg = load_config(paths.config_file)
    if cfg.error:
        checks.append(
            Check(
                "Configuration",
                Level.ERROR,
                f"{cfg.path}: {cfg.error}",
                "Fix the file or reset it in Settings",
            )
        )
    elif cfg.existed:
        checks.append(Check("Configuration", Level.OK, f"valid ({cfg.path})"))
    else:
        checks.append(Check("Configuration", Level.INFO, "no config file yet; using defaults"))

    try:
        conn = sqlite3.connect(f"file:{paths.database}?mode=ro", uri=True, timeout=2)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()
        level = Level.OK if result == "ok" else Level.ERROR
        checks.append(
            Check("Database", level, f"{result}, schema v{version}, {count} history rows")
        )
    except sqlite3.Error as exc:
        if paths.database.exists():
            checks.append(
                Check(
                    "Database",
                    Level.ERROR,
                    f"cannot read {paths.database}: {exc}",
                    "Start TurnPing; it moves an unreadable database aside and starts fresh",
                )
            )
        else:
            checks.append(Check("Database", Level.INFO, "not created yet (created on first start)"))

    backend = toast_backend or WindowsToastBackend()
    st = backend.status()
    if st.available:
        checks.append(Check("Windows notifications", Level.OK, st.detail))
    else:
        checks.append(
            Check(
                "Windows notifications",
                Level.WARN,
                f"{st.detail.rstrip('.')}. TurnPing will use its own popup window while the app is running.",
                st.fix,
            )
        )
    if sys.platform == "win32":
        checks.append(
            Check(
                "Toast app id",
                Level.OK if app_id_registered() else Level.INFO,
                "registered" if app_id_registered() else "registers when the app first starts",
            )
        )
        handler = win.uri_scheme_command(URI_SCHEME)
        if handler is None:
            checks.append(
                Check(
                    "Click-to-return handler",
                    Level.INFO,
                    f"{URI_SCHEME}:// not registered yet (registers when the app starts)",
                )
            )
        else:
            from turnping.installation.validation import executable_exists

            ok = executable_exists(handler)
            checks.append(
                Check(
                    "Click-to-return handler",
                    Level.OK if ok else Level.ERROR,
                    handler if ok else f"points to a missing program: {handler}",
                    None if ok else "Start TurnPing once to re-register",
                )
            )
        startup = win.get_startup()
        checks.append(Check("Start with Windows", Level.INFO, "enabled" if startup else "disabled"))

    if app_running is None:
        from turnping.ingest.ipc import is_app_running

        app_running = is_app_running()
    checks.append(
        Check(
            "Desktop app",
            Level.OK if app_running else Level.INFO,
            "running (reminders and popups active)"
            if app_running
            else "not running; hooks will start it on the next event",
            None if app_running else "turnping run",
        )
    )

    for report in integration_statuses(repo):
        level = {
            IntegrationStatus.OPERATIONAL: Level.OK,
            IntegrationStatus.INSTALLED_UNVERIFIED: Level.INFO,
            IntegrationStatus.NOT_INSTALLED: Level.INFO,
            IntegrationStatus.MISCONFIGURED: Level.ERROR,
            IntegrationStatus.UNSUPPORTED: Level.WARN,
        }[report.status]
        checks.append(
            Check(
                f"Integration: {report.display_name}",
                level,
                f"{report.status.value}: {report.detail}",
            )
        )

    failures = recent_hook_failures(paths)
    if failures:
        last = failures[-1]
        checks.append(
            Check(
                "Recent hook failures",
                Level.WARN,
                f"{len(failures)} recent; last at {last.get('at')} from {last.get('provider')}: {last.get('reason')}",
                f"See {paths.hook_failures_file}",
            )
        )
    else:
        checks.append(Check("Recent hook failures", Level.OK, "none"))
    return checks


def make_test_event(category: str = "TASK_COMPLETED"):
    """A clearly labelled event that exercises the real pipeline end to end."""
    import os

    from turnping.adapters.registry import TEST_PROVIDER_ID
    from turnping.contracts.events import AgentEvent, EventCategory
    from turnping.platform.windows import find_host_window

    cwd = os.getcwd()
    return AgentEvent(
        provider=TEST_PROVIDER_ID,
        native_type="test-notification",
        category=EventCategory(category),
        session_id="test",
        project_path=cwd,
        message="TurnPing test notification. If you can see this, notifications work.",
        metadata={"test": True},
        host=find_host_window(cwd),
    )


def format_checks(checks: list[Check]) -> str:
    icons = {Level.OK: "[ OK ]", Level.INFO: "[INFO]", Level.WARN: "[WARN]", Level.ERROR: "[FAIL]"}
    lines = []
    for c in checks:
        lines.append(f"{icons[c.level]} {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"        fix: {c.fix}")
    return "\n".join(lines)


def data_locations(paths: AppPaths | None = None) -> dict[str, Path]:
    paths = paths or get_paths()
    return {
        "data directory": paths.home,
        "configuration": paths.config_file,
        "history database": paths.database,
        "logs": paths.log_dir,
        "backups of agent settings": paths.backup_dir,
    }
