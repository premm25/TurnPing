"""Background engine thread: owns the router, reminder ticks, spool draining, pruning.

All pipeline work happens here, never on the GUI thread. The queue is bounded: if it
fills up (a runaway integration), new events are dropped and counted rather than
growing memory without limit.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from turnping.contracts.events import AgentEvent
from turnping.core.router import EventRouter
from turnping.ingest.spool import Spool

log = logging.getLogger(__name__)

_STOP = object()


class Engine:
    def __init__(
        self,
        router: EventRouter,
        *,
        spool: Spool | None = None,
        tick_seconds: float = 1.0,
        spool_interval: float = 5.0,
        prune_interval: float = 3600.0,
        max_queue: int = 1000,
    ) -> None:
        self.router = router
        self.spool = spool
        self.tick_seconds = tick_seconds
        self.spool_interval = spool_interval
        self.prune_interval = prune_interval
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.processed = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(target=self._run, name="turnping-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running:
            return
        try:
            self._queue.put(_STOP, timeout=1.0)
        except queue.Full:
            pass
        assert self._thread is not None
        self._thread.join(timeout)
        cancelled = self.router.reminders.cancel_all()
        if cancelled:
            log.info("cancelled %d pending reminder(s) on shutdown", cancelled)

    def submit(self, event: AgentEvent) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
            log.error(
                "engine queue full; dropped %s event from %s", event.category.value, event.provider
            )
            return False

    def call(self, fn: Callable[[], Any]) -> bool:
        """Run ``fn`` on the engine thread (serialized with event processing)."""
        try:
            self._queue.put_nowait(fn)
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        last_spool = 0.0
        last_prune = 0.0
        while True:
            try:
                item = self._queue.get(timeout=self.tick_seconds)
            except queue.Empty:
                item = None
            if item is _STOP:
                break
            try:
                if isinstance(item, AgentEvent):
                    self.router.process(item)
                    self.processed += 1
                elif callable(item):
                    item()
                self.router.fire_due_reminders()
                mono = time.monotonic()
                if self.spool is not None and mono - last_spool >= self.spool_interval:
                    last_spool = mono
                    for event in self.spool.drain():
                        self.router.process(event)
                        self.processed += 1
                if mono - last_prune >= self.prune_interval:
                    last_prune = mono
                    cfg = self.router.config_getter()
                    removed = self.router.repo.prune(
                        cfg.history.retention_days, cfg.history.max_events
                    )
                    if removed:
                        log.info("pruned %d old history rows", removed)
            except Exception as exc:  # keep the loop alive no matter what
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                log.exception("engine loop error")
