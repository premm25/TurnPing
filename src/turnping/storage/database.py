"""SQLite connection management with corruption recovery.

If the database cannot be opened or fails ``PRAGMA quick_check``, it is moved aside to
``turnping.corrupt-<timestamp>.db`` (never deleted) and a fresh database is created.
The recovery is reported through :attr:`Database.recovery_note` so the dashboard and
``turnping doctor`` can tell the user where the old file went.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from turnping.storage.migrations import migrate

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        self._target = str(path)
        self._lock = threading.RLock()
        self.recovery_note: str | None = None
        self._conn = self._open_with_recovery()

    def _connect(self) -> sqlite3.Connection:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._target, timeout=5.0, check_same_thread=False, isolation_level=None
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            if self.path is not None:
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute("PRAGMA quick_check").fetchone()
            if row is None or row[0] != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {row[0] if row else '?'}")
        except BaseException:
            conn.close()  # release the file handle so a corrupt file can be moved aside
            raise
        return conn

    def _open_with_recovery(self) -> sqlite3.Connection:
        try:
            conn = self._connect()
        except sqlite3.DatabaseError as exc:
            if self.path is None:
                raise
            log.error("database %s is unusable (%s); moving it aside", self.path, exc)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            target = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}{self.path.suffix}")
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(self.path) + suffix)
                if src.exists():
                    src.replace(Path(str(target) + suffix))
            self.recovery_note = (
                f"The history database was unreadable and was moved to {target}. "
                "A new empty database was created. The old file can be inspected with any "
                "SQLite tool."
            )
            conn = self._connect()
        try:
            migrate(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
