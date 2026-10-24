"""Builds the object graph shared by the desktop app, the CLI and the headless hook."""

from __future__ import annotations

import logging
import logging.handlers
import threading
from dataclasses import dataclass, field
from pathlib import Path

from turnping.adapters.registry import AdapterRegistry, default_registry
from turnping.config import AppConfig, ConfigLoadResult, load_config
from turnping.core.dispatcher import Dispatcher
from turnping.core.reminders import ReminderScheduler
from turnping.core.router import EventRouter
from turnping.notifications.manager import NotificationManager
from turnping.notifications.windows_toast import WindowsToastBackend
from turnping.security.paths import AppPaths, get_paths
from turnping.storage.database import Database
from turnping.storage.repository import Repository


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from turnping.security.redaction import redact_text

        return redact_text(super().format(record))


def setup_logging(paths: AppPaths, level: str = "INFO", *, name: str = "turnping.log") -> None:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    close_logging()
    root = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler(
        paths.log_dir / name, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._turnping = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


def close_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_turnping", False):
            root.removeHandler(h)
            h.close()


@dataclass
class Runtime:
    paths: AppPaths
    config_result: ConfigLoadResult
    db: Database
    repo: Repository
    notifier: NotificationManager
    router: EventRouter
    registry: AdapterRegistry
    _config_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def config(self) -> AppConfig:
        with self._config_lock:
            return self.config_result.config

    def reload_config(self) -> ConfigLoadResult:
        result = load_config(self.paths.config_file)
        with self._config_lock:
            self.config_result = result
        return result

    def set_config(self, config: AppConfig) -> None:
        with self._config_lock:
            self.config_result = ConfigLoadResult(
                config=config, path=self.paths.config_file, existed=True
            )

    def close(self) -> None:
        self.db.close()


def build_runtime(
    *,
    paths: AppPaths | None = None,
    toast: bool = True,
    db_path: Path | str | None = None,
) -> Runtime:
    paths = (paths or get_paths()).ensure()
    config_result = load_config(paths.config_file)
    db = Database(db_path or paths.database)
    repo = Repository(db)
    registry = default_registry()
    holder: dict[str, Runtime] = {}

    def mode() -> str:
        return holder["rt"].config.general.notification_backend if "rt" in holder else "auto"

    notifier = NotificationManager(mode_getter=mode)
    if toast:
        notifier.add_backend("toast", WindowsToastBackend())

    def config_getter() -> AppConfig:
        return holder["rt"].config

    router = EventRouter(
        repo,
        config_getter,
        Dispatcher(notifier),
        reminders=ReminderScheduler(),
        registry=registry,
    )
    rt = Runtime(paths, config_result, db, repo, notifier, router, registry)
    holder["rt"] = rt
    return rt
