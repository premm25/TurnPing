"""Versioned schema migrations. Append new steps; never edit a released one."""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE events (
        event_id        TEXT PRIMARY KEY,
        provider        TEXT NOT NULL,
        native_type     TEXT NOT NULL,
        category        TEXT NOT NULL,
        session_key     TEXT NOT NULL,
        session_id      TEXT,
        project_path    TEXT,
        occurred_at     TEXT NOT NULL,
        received_at     TEXT NOT NULL,
        title           TEXT,
        message         TEXT,
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        dedup_key       TEXT,
        decision        TEXT NOT NULL,
        decision_reason TEXT,
        priority        TEXT,
        delivery_status TEXT,
        delivery_detail TEXT,
        is_reminder     INTEGER NOT NULL DEFAULT 0,
        dismissed       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_events_received ON events(received_at DESC);
    CREATE INDEX idx_events_session ON events(session_key, received_at DESC);
    CREATE INDEX idx_events_dedup ON events(dedup_key);

    CREATE TABLE sessions (
        session_key      TEXT PRIMARY KEY,
        provider         TEXT NOT NULL,
        session_id       TEXT,
        key_inferred     INTEGER NOT NULL DEFAULT 0,
        project_path     TEXT,
        state            TEXT NOT NULL,
        first_seen       TEXT NOT NULL,
        last_seen        TEXT NOT NULL,
        last_event_id    TEXT,
        last_category    TEXT,
        host_json        TEXT
    );
    CREATE INDEX idx_sessions_last_seen ON sessions(last_seen DESC);

    CREATE TABLE app_state (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    version = current_version(conn)
    if version > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema v{version} is newer than this TurnPing (v{len(MIGRATIONS)}); "
            "upgrade TurnPing or move the database aside"
        )
    for index in range(version, len(MIGRATIONS)):
        try:
            conn.executescript(
                "BEGIN;" + MIGRATIONS[index] + f"; PRAGMA user_version = {index + 1}; COMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return current_version(conn)
