"""The provider-independent TurnPing event contract (schema version 1).

Every adapter translates its native payload into an :class:`AgentEvent`. The core never
sees provider payloads. See ``docs/EVENT_CONTRACTS.md`` for the human-readable spec.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from turnping.security.redaction import sanitize_message

SCHEMA_VERSION: Final = 1

PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
MAX_PROVIDER_ID_LEN = 40
MAX_NATIVE_TYPE_LEN = 80
MAX_SESSION_ID_LEN = 200
MAX_PATH_LEN = 1024
MAX_METADATA_KEYS = 16
MAX_METADATA_VALUE_LEN = 200


class EventCategory(StrEnum):
    RESPONSE_STOPPED = "RESPONSE_STOPPED"
    TASK_COMPLETED = "TASK_COMPLETED"
    INPUT_NEEDED = "INPUT_NEEDED"
    PERMISSION_NEEDED = "PERMISSION_NEEDED"
    TASK_FAILED = "TASK_FAILED"
    SESSION_RESUMED = "SESSION_RESUMED"
    SESSION_ENDED = "SESSION_ENDED"
    UNKNOWN = "UNKNOWN"

    @property
    def needs_attention(self) -> bool:
        """Events that block the agent until the human acts."""
        return self in (EventCategory.INPUT_NEEDED, EventCategory.PERMISSION_NEEDED)

    @property
    def label(self) -> str:
        return _CATEGORY_LABELS[self]


_CATEGORY_LABELS = {
    EventCategory.RESPONSE_STOPPED: "Response finished",
    EventCategory.TASK_COMPLETED: "Task completed",
    EventCategory.INPUT_NEEDED: "Input needed",
    EventCategory.PERMISSION_NEEDED: "Permission needed",
    EventCategory.TASK_FAILED: "Failure reported",
    EventCategory.SESSION_RESUMED: "Session active",
    EventCategory.SESSION_ENDED: "Session ended",
    EventCategory.UNKNOWN: "Unknown event",
}


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        return ["low", "normal", "high", "urgent"].index(self.value)


MetadataValue = str | int | float | bool | None


class HostWindow(BaseModel):
    """Where the session's terminal/editor window lives, captured at hook time.

    Used only for click-to-return. All fields are optional; navigation degrades to a
    details view when nothing reliable is known.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    hwnd: int | None = Field(default=None, ge=0)
    pid: int | None = Field(default=None, ge=0)
    process_name: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=256)
    terminal: str | None = Field(default=None, max_length=64)  # e.g. "vscode", "windows-terminal"

    @field_validator("title", "process_name", "terminal", mode="before")
    @classmethod
    def _clean(cls, v: object) -> str | None:
        return sanitize_message(v, limit=256) if v is not None else None


class AgentEvent(BaseModel):
    """A normalized, validated lifecycle event from any coding agent."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=64)
    provider: str = Field(min_length=1, max_length=MAX_PROVIDER_ID_LEN)
    native_type: str = Field(min_length=1, max_length=MAX_NATIVE_TYPE_LEN)
    category: EventCategory
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str | None = Field(default=None, max_length=MAX_SESSION_ID_LEN)
    project_path: str | None = Field(default=None, max_length=MAX_PATH_LEN)
    message: str | None = None
    title: str | None = None
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    dedup_key: str | None = Field(default=None, max_length=300)
    host: HostWindow | None = None

    @field_validator("provider")
    @classmethod
    def _provider_id(cls, v: str) -> str:
        v = v.lower()
        if not PROVIDER_ID_PATTERN.match(v):
            raise ValueError("provider must be lowercase letters/digits separated by - or _")
        return v

    @field_validator("native_type")
    @classmethod
    def _native_type(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.:\-/ ]+", v):
            raise ValueError("native_type contains unsupported characters")
        return v

    @field_validator("event_id")
    @classmethod
    def _event_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("event_id must be alphanumeric")
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, v: object) -> object:
        if v is None or v == "":
            return datetime.now(UTC)
        if isinstance(v, (int, float)):
            # Epoch seconds, or milliseconds if implausibly large.
            seconds = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(seconds, tz=UTC)
        if isinstance(v, str):
            text = v.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
        return v

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            # Naive timestamps are assumed to be UTC rather than local time.
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @field_validator("session_id", "dedup_key", mode="before")
    @classmethod
    def _opt_id(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, (str, int)):
            raise ValueError("identifier must be a string")
        text = "".join(ch for ch in str(v) if ch.isprintable()).strip()
        return text or None

    @field_validator("project_path", mode="before")
    @classmethod
    def _path(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("project_path must be a string")
        text = "".join(ch for ch in v if ch.isprintable()).strip()
        return text or None

    @field_validator("message", "title", mode="before")
    @classmethod
    def _message(cls, v: object) -> str | None:
        return sanitize_message(v)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, v: object) -> dict[str, MetadataValue]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("metadata must be an object")
        out: dict[str, MetadataValue] = {}
        for key, value in list(v.items())[:MAX_METADATA_KEYS]:
            k = str(key)[:64]
            if not re.fullmatch(r"[A-Za-z0-9_.\-]+", k):
                continue
            if isinstance(value, str):
                out[k] = sanitize_message(value, limit=MAX_METADATA_VALUE_LEN)
            elif isinstance(value, (bool, int, float)) or value is None:
                out[k] = value
            # nested structures are silently dropped: metadata is flat by contract
        return out

    # ----- derived helpers -------------------------------------------------

    @property
    def session_key(self) -> str:
        """Stable key separating concurrent sessions.

        Uses the provider's session id when available. Otherwise falls back to the
        project path, which cannot distinguish two sessions in the same directory.
        """
        if self.session_id:
            return f"{self.provider}:{self.session_id}"
        if self.project_path:
            return f"{self.provider}:path:{self.project_path.lower().replace(chr(92), '/')}"
        return f"{self.provider}:unknown"

    @property
    def session_key_inferred(self) -> bool:
        return not self.session_id

    @property
    def project_name(self) -> str | None:
        if not self.project_path:
            return None
        cleaned = self.project_path.replace("\\", "/").rstrip("/")
        return cleaned.rsplit("/", 1)[-1] or cleaned

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_wire(cls, data: Any) -> AgentEvent:
        return cls.model_validate(data)
