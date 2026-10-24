"""Google Gemini CLI adapter.

Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md
(checked 2026-10-24). Hooks receive JSON on stdin with ``session_id``, ``cwd``,
``hook_event_name`` and ``timestamp``. Stdout must contain *only* JSON, so the hook
always prints ``{}`` (which means "no decision": allow / continue unchanged).

* ``AfterAgent`` fires once per turn after the final response -> RESPONSE_STOPPED.
* ``Notification`` with ``notification_type == "ToolPermission"`` -> PERMISSION_NEEDED.
* ``BeforeAgent`` (prompt submitted) and ``AfterTool`` prove activity -> SESSION_RESUMED.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentping.adapters.base import (
    AdapterError,
    AdapterOptions,
    HookResponse,
    ProviderAdapter,
    ProviderCapabilities,
    optional_str,
)
from agentping.contracts.events import AgentEvent, EventCategory
from agentping.security.redaction import sanitize_message

C = EventCategory

ACTIVITY_EVENTS = frozenset({"BeforeAgent", "AfterTool", "BeforeTool"})
IGNORED_EVENTS = frozenset({"BeforeModel", "AfterModel", "BeforeToolSelection", "PreCompress"})


class GeminiCliAdapter(ProviderAdapter):
    provider_id = "gemini-cli"
    display_name = "Gemini CLI"
    capabilities = ProviderCapabilities(
        categories=frozenset(
            {C.RESPONSE_STOPPED, C.PERMISSION_NEEDED, C.SESSION_RESUMED, C.SESSION_ENDED}
        ),
        # No resolution signals: Gemini CLI hooks are synchronous, so AgentPing does not
        # install a per-tool AfterTool hook (it would slow every tool call). Without it,
        # "permission granted, agent still working" is indistinguishable from "still
        # blocked", so reminder escalation is disabled for Gemini CLI by design.
        resolution_signals=frozenset(),
    )

    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        options = options or AdapterOptions()
        if not isinstance(payload, Mapping):
            raise AdapterError("payload must be a JSON object")
        hook = optional_str(payload, "hook_event_name")
        if not hook:
            raise AdapterError("missing or invalid required field 'hook_event_name'")
        meta: dict[str, Any] = {}
        message: str | None = None

        if hook == "AfterAgent":
            if payload.get("stop_hook_active") is True:
                meta["retry"] = True
            category = C.RESPONSE_STOPPED
            message = "Gemini finished responding."
            if options.include_message_preview:
                message = sanitize_message(payload.get("prompt_response"), 160) or message
        elif hook == "Notification":
            ntype = optional_str(payload, "notification_type") or ""
            if ntype != "ToolPermission":
                return None
            category = C.PERMISSION_NEEDED
            meta["notification_type"] = ntype
            details = payload.get("details")
            if isinstance(details, Mapping):
                tool = details.get("tool_name") or details.get("toolName") or details.get("name")
                if isinstance(tool, str):
                    meta["tool_name"] = tool[:80]
            message = sanitize_message(payload.get("message")) or "Gemini needs your permission."
        elif hook == "SessionStart":
            category = C.SESSION_RESUMED
            source = optional_str(payload, "source")
            if source:
                meta["source"] = source
        elif hook == "SessionEnd":
            category = C.SESSION_ENDED
            reason = optional_str(payload, "reason")
            if reason:
                meta["reason"] = reason
        elif hook in ACTIVITY_EVENTS:
            category = C.SESSION_RESUMED
            meta["activity"] = hook
        elif hook in IGNORED_EVENTS:
            return None
        else:
            category = C.UNKNOWN

        try:
            return AgentEvent(
                provider=self.provider_id,
                native_type=hook,
                category=category,
                session_id=optional_str(payload, "session_id"),
                project_path=optional_str(payload, "cwd"),
                timestamp=payload.get("timestamp")
                if isinstance(payload.get("timestamp"), str)
                else None,
                message=message,
                metadata=meta,
            )
        except ValueError as exc:
            raise AdapterError(f"payload failed validation: {exc.__class__.__name__}") from exc

    def hook_response(self, payload: Mapping[str, Any] | None) -> HookResponse:
        return HookResponse(stdout="{}")
