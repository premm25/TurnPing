"""Generic adapter for user scripts and future agents.

Accepts the documented generic payload (see docs/EVENT_CONTRACTS.md, "Generic
ingestion")::

    {
      "provider": "my-tool",            # required, lowercase id, not a built-in id
      "category": "TASK_COMPLETED",     # required, one of EventCategory
      "native_type": "build-finished",  # optional
      "session_id": "abc",              # optional
      "project_path": "C:/src/app",     # optional
      "message": "Build finished",      # optional, sanitized + truncated
      "title": "...",                   # optional
      "event_id": "...",                # optional; reused as the dedup key
      "dedup_key": "...",               # optional
      "timestamp": "2026-10-24T10:00:00Z",
      "metadata": {"k": "v"}            # optional, flat scalars only
    }

Payload values are data only. Nothing in them is ever executed or interpreted as
configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentping.adapters.base import (
    AdapterError,
    AdapterOptions,
    ProviderAdapter,
    ProviderCapabilities,
)
from agentping.contracts.events import AgentEvent, EventCategory

ALLOWED_KEYS = frozenset(
    {
        "provider",
        "category",
        "native_type",
        "session_id",
        "project_path",
        "message",
        "title",
        "event_id",
        "dedup_key",
        "timestamp",
        "metadata",
        "schema_version",
    }
)


class GenericAdapter(ProviderAdapter):
    provider_id = "generic"
    display_name = "Generic / custom scripts"
    capabilities = ProviderCapabilities(
        categories=frozenset(EventCategory) - {EventCategory.UNKNOWN},
        resolution_signals=frozenset({EventCategory.SESSION_RESUMED}),
    )

    def __init__(self, reserved_provider_ids: frozenset[str] = frozenset()) -> None:
        self._reserved = reserved_provider_ids

    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        if not isinstance(payload, Mapping):
            raise AdapterError("payload must be a JSON object")
        unknown = set(payload) - ALLOWED_KEYS
        if unknown:
            raise AdapterError(f"unknown fields: {', '.join(sorted(unknown)[:5])}")
        provider = payload.get("provider")
        if not isinstance(provider, str):
            raise AdapterError("missing or invalid required field 'provider'")
        provider = provider.strip().lower()
        if provider in self._reserved:
            raise AdapterError(f"provider id '{provider}' is reserved for a built-in integration")
        raw_category = payload.get("category")
        if not isinstance(raw_category, str):
            raise AdapterError("missing or invalid required field 'category'")
        try:
            category = EventCategory(raw_category.strip().upper())
        except ValueError as exc:
            raise AdapterError(f"unknown category '{raw_category[:40]}'") from exc
        if payload.get("schema_version", 1) != 1:
            raise AdapterError("unsupported schema_version")

        fields: dict[str, Any] = {
            "provider": provider,
            "category": category,
            "native_type": payload.get("native_type") or f"generic:{category.value}",
        }
        for key in ("session_id", "project_path", "message", "title", "timestamp", "metadata"):
            if payload.get(key) is not None:
                fields[key] = payload[key]
        event_id = payload.get("event_id")
        if event_id is not None:
            fields["event_id"] = event_id
        dedup = payload.get("dedup_key") or (f"{provider}:{event_id}" if event_id else None)
        if dedup is not None:
            fields["dedup_key"] = dedup
        try:
            return AgentEvent(**fields)
        except ValueError as exc:
            errors = getattr(exc, "errors", None)
            locs = (
                ", ".join(".".join(str(p) for p in e.get("loc", ())) for e in errors()[:5])
                if callable(errors)
                else ""
            )
            raise AdapterError(f"invalid fields: {locs or 'validation failed'}") from exc
