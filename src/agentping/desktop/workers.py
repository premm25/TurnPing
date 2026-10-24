"""Run blocking work (SQLite, file I/O, registry) off the GUI thread."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:
            log.exception("background task failed")
            self.signals.failed.emit(f"{exc.__class__.__name__}: {exc}")
        else:
            self.signals.done.emit(result)


_live: set[_Task] = set()


def run_async(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Run ``fn`` on the thread pool; callbacks are delivered on the GUI thread."""
    task = _Task(fn)
    task.setAutoDelete(False)
    _live.add(task)

    def finish() -> None:
        _live.discard(task)

    if on_done is not None:
        task.signals.done.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    task.signals.done.connect(lambda _r: finish())
    task.signals.failed.connect(lambda _e: finish())
    QThreadPool.globalInstance().start(task)
