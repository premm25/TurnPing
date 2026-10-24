"""Repository: the only module that issues SQL. All queries are parameterized."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from turnping.contracts.events import AgentEvent, HostWindow
from turnping.storage.database import Database


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def _parse(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    provider: str
    native_type: str
    category: str
    session_key: str
    session_id: str | None
    project_path: str | None
    occurred_at: datetime
    received_at: datetime
    title: str | None
    message: str | None
    metadata: dict[str, Any]
    decision: str
    decision_reason: str | None
    priority: str | None
    delivery_status: str | None
    delivery_detail: str | None
    is_reminder: bool
    dismissed: bool

    @classmethod
    def from_row(cls, row: Any) -> EventRecord:
        return cls(
            event_id=row["event_id"],
            provider=row["provider"],
            native_type=row["native_type"],
            category=row["category"],
            session_key=row["session_key"],
            session_id=row["session_id"],
            project_path=row["project_path"],
            occurred_at=_parse(row["occurred_at"]) or datetime.now(UTC),
            received_at=_parse(row["received_at"]) or datetime.now(UTC),
            title=row["title"],
            message=row["message"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            decision=row["decision"],
            decision_reason=row["decision_reason"],
            priority=row["priority"],
            delivery_status=row["delivery_status"],
            delivery_detail=row["delivery_detail"],
            is_reminder=bool(row["is_reminder"]),
            dismissed=bool(row["dismissed"]),
        )


@dataclass(frozen=True)
class SessionRecord:
    session_key: str
    provider: str
    session_id: str | None
    key_inferred: bool
    project_path: str | None
    state: str
    first_seen: datetime
    last_seen: datetime
    last_event_id: str | None
    last_category: str | None
    host: HostWindow | None

    @classmethod
    def from_row(cls, row: Any) -> SessionRecord:
        host = None
        if row["host_json"]:
            try:
                host = HostWindow.model_validate_json(row["host_json"])
            except ValueError:
                host = None
        return cls(
            session_key=row["session_key"],
            provider=row["provider"],
            session_id=row["session_id"],
            key_inferred=bool(row["key_inferred"]),
            project_path=row["project_path"],
            state=row["state"],
            first_seen=_parse(row["first_seen"]) or datetime.now(UTC),
            last_seen=_parse(row["last_seen"]) or datetime.now(UTC),
            last_event_id=row["last_event_id"],
            last_category=row["last_category"],
            host=host,
        )

    @property
    def project_name(self) -> str | None:
        if not self.project_path:
            return None
        return self.project_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----- events -------------------------------------------------------------

    def record_event(
        self,
        event: AgentEvent,
        *,
        decision: str,
        reason: str | None = None,
        priority: str | None = None,
        delivery_status: str | None = None,
        delivery_detail: str | None = None,
        is_reminder: bool = False,
        received_at: datetime | None = None,
        event_id: str | None = None,
    ) -> str:
        eid = event_id or event.event_id
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO events (event_id, provider, native_type, category,
                       session_key, session_id, project_path, occurred_at, received_at, title,
                       message, metadata_json, dedup_key, decision, decision_reason, priority,
                       delivery_status, delivery_detail, is_reminder)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    eid,
                    event.provider,
                    event.native_type,
                    event.category.value,
                    event.session_key,
                    event.session_id,
                    event.project_path,
                    _iso(event.timestamp),
                    _iso(received_at or datetime.now(UTC)),
                    event.title,
                    event.message,
                    json.dumps(event.metadata, sort_keys=True),
                    event.dedup_key,
                    decision,
                    reason,
                    priority,
                    delivery_status,
                    delivery_detail,
                    int(is_reminder),
                ),
            )
        return eid

    def update_delivery(self, event_id: str, status: str, detail: str | None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE events SET delivery_status = ?, delivery_detail = ? WHERE event_id = ?",
                (status, detail, event_id),
            )

    def mark_dismissed(self, event_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE events SET dismissed = 1 WHERE event_id = ?", (event_id,))

    def event_exists(self, event_id: str) -> bool:
        return bool(self.db.query("SELECT 1 FROM events WHERE event_id = ?", (event_id,)))

    def dedup_key_seen(self, dedup_key: str, since: datetime) -> bool:
        rows = self.db.query(
            "SELECT 1 FROM events WHERE dedup_key = ? AND received_at >= ? AND is_reminder = 0 LIMIT 1",
            (dedup_key, _iso(since)),
        )
        return bool(rows)

    def similar_event_seen(
        self, session_key: str, native_type: str, message: str | None, since: datetime
    ) -> bool:
        rows = self.db.query(
            """SELECT 1 FROM events WHERE session_key = ? AND native_type = ?
                   AND COALESCE(message, '') = ? AND received_at >= ? AND is_reminder = 0 LIMIT 1""",
            (session_key, native_type, message or "", _iso(since)),
        )
        return bool(rows)

    def get_event(self, event_id: str) -> EventRecord | None:
        rows = self.db.query("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return EventRecord.from_row(rows[0]) if rows else None

    def list_events(
        self,
        *,
        limit: int = 200,
        provider: str | None = None,
        category: str | None = None,
        search: str | None = None,
        session_key: str | None = None,
    ) -> list[EventRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if session_key:
            clauses.append("session_key = ?")
            params.append(session_key)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = "%" + escaped + "%"
            clauses.append(
                "(COALESCE(message,'') LIKE ? ESCAPE '\\' OR COALESCE(project_path,'') LIKE ? ESCAPE '\\'"
                " OR COALESCE(session_id,'') LIKE ? ESCAPE '\\' OR native_type LIKE ? ESCAPE '\\')"
            )
            params += [like, like, like, like]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 10_000)))
        rows = self.db.query(
            f"SELECT * FROM events {where} ORDER BY received_at DESC LIMIT ?",  # noqa: S608 - clauses are constants
            tuple(params),
        )
        return [EventRecord.from_row(r) for r in rows]

    def count_events(self) -> int:
        return int(self.db.query("SELECT COUNT(*) FROM events")[0][0])

    def clear_history(self) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM sessions")
            return cur.rowcount

    def prune(self, retention_days: int, max_events: int, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        cutoff = _iso(now - timedelta(days=retention_days))
        with self.db.transaction() as conn:
            removed = conn.execute("DELETE FROM events WHERE received_at < ?", (cutoff,)).rowcount
            removed += conn.execute(
                """DELETE FROM events WHERE event_id IN (
                       SELECT event_id FROM events ORDER BY received_at DESC LIMIT -1 OFFSET ?)""",
                (max_events,),
            ).rowcount
            conn.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
        return removed

    def provider_last_seen(self, provider: str) -> datetime | None:
        rows = self.db.query("SELECT MAX(last_seen) FROM sessions WHERE provider = ?", (provider,))
        return _parse(rows[0][0]) if rows and rows[0][0] else None

    # ----- sessions -----------------------------------------------------------

    def upsert_session(self, event: AgentEvent, state: str, now: datetime) -> None:
        host_json = event.host.model_dump_json() if event.host else None
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO sessions (session_key, provider, session_id, key_inferred,
                       project_path, state, first_seen, last_seen, last_event_id, last_category,
                       host_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_key) DO UPDATE SET
                       state = excluded.state,
                       last_seen = excluded.last_seen,
                       project_path = COALESCE(excluded.project_path, sessions.project_path),
                       last_event_id = COALESCE(excluded.last_event_id, sessions.last_event_id),
                       last_category = excluded.last_category,
                       host_json = COALESCE(excluded.host_json, sessions.host_json)""",
                (
                    event.session_key,
                    event.provider,
                    event.session_id,
                    int(event.session_key_inferred),
                    event.project_path,
                    state,
                    _iso(now),
                    _iso(now),
                    event.event_id,
                    event.category.value,
                    host_json,
                ),
            )

    def get_session(self, session_key: str) -> SessionRecord | None:
        rows = self.db.query("SELECT * FROM sessions WHERE session_key = ?", (session_key,))
        return SessionRecord.from_row(rows[0]) if rows else None

    def list_sessions(self, limit: int = 50) -> list[SessionRecord]:
        rows = self.db.query("SELECT * FROM sessions ORDER BY last_seen DESC LIMIT ?", (limit,))
        return [SessionRecord.from_row(r) for r in rows]

    # ----- app state ----------------------------------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        rows = self.db.query("SELECT value FROM app_state WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set_state(self, key: str, value: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
