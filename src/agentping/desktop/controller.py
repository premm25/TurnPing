"""Mediates between the Qt UI and the engine/runtime.

Everything that touches SQLite, the registry or agent config files runs either on the
engine thread (``engine.call``) or the thread pool (``run_async``). The GUI thread only
receives results through queued signals.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from agentping.config import AppConfig, quarantine_broken_config, save_config
from agentping.contracts.events import AgentEvent
from agentping.core.engine import Engine
from agentping.core.health import Check, integration_statuses, make_test_event, run_checks
from agentping.core.navigation import NavigationResult, navigate_to_event
from agentping.core.router import ProcessingResult
from agentping.desktop.workers import run_async
from agentping.installation import system
from agentping.installation.hooks import INSTALLERS, InstallReport, StatusReport
from agentping.notifications.windows_toast import WindowsToastBackend
from agentping.runtime import Runtime, setup_logging
from agentping.storage.repository import EventRecord, SessionRecord

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardData:
    checks: list[Check]
    integrations: list[StatusReport]
    sessions: list[SessionRecord]
    recent: list[EventRecord]
    pending_reminders: int
    paused: bool
    total_events: int
    recovery_note: str | None
    config_error: str | None


@dataclass(frozen=True)
class SessionDetails:
    event: EventRecord | None
    session: SessionRecord | None
    reason: str


class Controller(QObject):
    event_processed = Signal(object)
    paused_changed = Signal(bool)
    config_changed = Signal(object)
    session_details_requested = Signal(object)  # SessionDetails
    show_page_requested = Signal(str)
    quit_requested = Signal()
    status_message = Signal(str)

    def __init__(self, runtime: Runtime, engine: Engine) -> None:
        super().__init__()
        self.runtime = runtime
        self.engine = engine
        self._paused = runtime.router.paused
        runtime.router.listeners.append(self._on_processed)

    # ----- engine callbacks (engine thread) -------------------------------------------

    def _on_processed(self, result: ProcessingResult) -> None:
        self.event_processed.emit(result)  # queued to GUI thread

    # ----- state ----------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def config(self) -> AppConfig:
        return self.runtime.config

    def set_paused(self, paused: bool) -> None:
        def work() -> None:
            self.runtime.router.set_paused(paused)
            self._paused = paused
            self.paused_changed.emit(paused)

        self.engine.call(work)

    # ----- ingestion from IPC (IPC worker threads) ------------------------------------

    def submit_event(self, event: AgentEvent) -> bool:
        return self.engine.submit(event)

    def process_and_wait(self, event: AgentEvent, timeout: float = 8.0) -> ProcessingResult | None:
        box: list[ProcessingResult] = []
        done = threading.Event()

        def work() -> None:
            box.append(self.runtime.router.process(event))
            done.set()

        if not self.engine.call(work) or not done.wait(timeout):
            return None
        return box[0]

    # ----- actions --------------------------------------------------------------------

    def send_test(self, category: str, on_done: Callable[[ProcessingResult | None], None]) -> None:
        def work() -> ProcessingResult | None:
            return self.process_and_wait(make_test_event(category))

        run_async(work, on_done, lambda err: on_done(None))

    def acknowledge(self, event_id: str, *, dismissed: bool) -> None:
        self.engine.call(lambda: self.runtime.router.acknowledge(event_id, dismissed=dismissed))

    def open_event(self, event_id: str) -> None:
        """Navigate to the event's session; fall back to the details dialog."""
        repo = self.runtime.repo

        def work() -> tuple[NavigationResult, SessionDetails]:
            result, _ = navigate_to_event(repo, event_id)
            record = repo.get_event(event_id)
            session = repo.get_session(record.session_key) if record else None
            return result, SessionDetails(record, session, result.detail)

        def done(value: tuple[NavigationResult, SessionDetails]) -> None:
            result, details = value
            self.acknowledge(event_id, dismissed=False)
            if not result.ok:
                self.session_details_requested.emit(details)

        run_async(work, done)

    def show_event_details(self, event_id: str, reason: str) -> None:
        repo = self.runtime.repo

        def work() -> SessionDetails:
            record = repo.get_event(event_id)
            session = repo.get_session(record.session_key) if record else None
            return SessionDetails(record, session, reason)

        run_async(work, self.session_details_requested.emit)

    def open_session(self, session: SessionRecord) -> None:
        from agentping.core.navigation import navigate

        def work() -> NavigationResult:
            return navigate(session.host, session.project_path)

        def done(result: NavigationResult) -> None:
            if not result.ok:
                self.session_details_requested.emit(SessionDetails(None, session, result.detail))

        run_async(work, done)

    # ----- data loading ---------------------------------------------------------------

    def load_dashboard(self, on_done: Callable[[DashboardData], None]) -> None:
        rt = self.runtime
        toast = rt.notifier.backend("toast")

        def work() -> DashboardData:
            checks = run_checks(
                rt.paths,
                rt.repo,
                app_running=True,
                toast_backend=toast if isinstance(toast, WindowsToastBackend) else None,
            )
            return DashboardData(
                checks=checks,
                integrations=integration_statuses(rt.repo),
                sessions=rt.repo.list_sessions(30),
                recent=rt.repo.list_events(limit=12),
                pending_reminders=len(rt.router.reminders.pending()),
                paused=rt.router.paused,
                total_events=rt.repo.count_events(),
                recovery_note=rt.db.recovery_note,
                config_error=rt.config_result.error,
            )

        run_async(
            work, on_done, lambda err: self.status_message.emit(f"Could not load dashboard: {err}")
        )

    def load_history(
        self,
        on_done: Callable[[list[EventRecord]], None],
        *,
        provider: str | None,
        category: str | None,
        search: str | None,
        limit: int = 1000,
    ) -> None:
        repo = self.runtime.repo
        run_async(
            lambda: repo.list_events(
                limit=limit, provider=provider, category=category, search=search
            ),
            on_done,
            lambda err: self.status_message.emit(f"Could not load history: {err}"),
        )

    def clear_history(self, on_done: Callable[[int], None]) -> None:
        run_async(self.runtime.repo.clear_history, on_done)

    # ----- configuration --------------------------------------------------------------

    def save_config(
        self,
        config: AppConfig,
        *,
        start_with_windows: bool,
        on_done: Callable[[str | None], None],
    ) -> None:
        rt = self.runtime
        previous = rt.config
        had_error = rt.config_result.error is not None

        def work() -> str | None:
            if had_error:
                moved = quarantine_broken_config(rt.paths.config_file)
                if moved:
                    log.warning("previous invalid config copied to %s", moved)
            save_config(config, rt.paths.config_file)
            rt.set_config(config)
            setup_logging(rt.paths, config.general.log_level)
            if system.start_with_windows_enabled() != start_with_windows:
                system.set_start_with_windows(start_with_windows)
            # Changing idle alerts changes Claude Code's hook matcher: refresh if installed.
            if (
                previous.provider("claude-code").notify_idle_prompt
                != config.provider("claude-code").notify_idle_prompt
            ):
                installer = INSTALLERS["claude-code"]
                status = installer.status()
                if status.status.name in ("OPERATIONAL", "INSTALLED_UNVERIFIED", "MISCONFIGURED"):
                    installer.install(
                        {"notify_idle_prompt": config.provider("claude-code").notify_idle_prompt}
                    )
            self.config_changed.emit(config)
            return None

        run_async(work, on_done, on_done)

    def install_integration(self, provider: str, on_done: Callable[[InstallReport], None]) -> None:
        config = self.runtime.config

        def work() -> InstallReport:
            options = {"notify_idle_prompt": config.provider(provider).notify_idle_prompt}
            report = INSTALLERS[provider].install(options)
            system.register_system_integration()
            return report

        run_async(work, on_done)

    def uninstall_integration(
        self, provider: str, on_done: Callable[[InstallReport], None]
    ) -> None:
        run_async(INSTALLERS[provider].uninstall, on_done)

    def start_with_windows(self, on_done: Callable[[Any], None]) -> None:
        run_async(system.start_with_windows_enabled, on_done)
