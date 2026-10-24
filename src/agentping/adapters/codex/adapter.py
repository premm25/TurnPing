"""OpenAI Codex CLI adapter.

Sources: https://developers.openai.com/codex/hooks (checked 2026-10-24). Codex hooks
receive a JSON object on stdin with ``session_id``, ``cwd``, ``hook_event_name`` and,
for turn-scoped events, ``turn_id``.

Also accepts the legacy ``notify`` program payload (``{"type": "agent-turn-complete",
"thread-id": ..., "turn-id": ..., "cwd": ...}``) passed as a command-line argument.

Notes:
* ``Stop`` = the turn's response ended -> RESPONSE_STOPPED.
* ``PermissionRequest`` runs when Codex is about to ask for approval. If no hook decides,
  Codex uses its normal approval flow. AgentPing never decides.
* Codex's ``Stop`` requires JSON on stdout when exiting 0, so the hook prints ``{}``.
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

ACTIVITY_EVENTS = frozenset({"UserPromptSubmit", "PostToolUse", "PreToolUse", "Interrupt"})
IGNORED_EVENTS = frozenset({"SubagentStart", "SubagentStop", "PreCompact", "PostCompact"})
JSON_STDOUT_EVENTS = frozenset({"Stop"})


class CodexAdapter(ProviderAdapter):
    provider_id = "codex"
    display_name = "Codex CLI"
    capabilities = ProviderCapabilities(
        categories=frozenset(
            {C.RESPONSE_STOPPED, C.PERMISSION_NEEDED, C.SESSION_RESUMED, C.SESSION_ENDED}
        ),
        resolution_signals=frozenset({C.SESSION_RESUMED, C.RESPONSE_STOPPED}),
    )

    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        options = options or AdapterOptions()
        if not isinstance(payload, Mapping):
            raise AdapterError("payload must be a JSON object")
        if "hook_event_name" not in payload and payload.get("type") == "agent-turn-complete":
            return self._translate_notify(payload, options)

        hook = optional_str(payload, "hook_event_name")
        if not hook:
            raise AdapterError("missing or invalid required field 'hook_event_name'")
        session_id = optional_str(payload, "session_id")
        turn = optional_str(payload, "turn_id")
        meta: dict[str, Any] = {}
        mode = optional_str(payload, "permission_mode")
        if mode:
            meta["permission_mode"] = mode
        message: str | None = None

        if hook == "Stop":
            category = C.RESPONSE_STOPPED
            message = "Codex finished responding."
            if options.include_message_preview:
                message = sanitize_message(payload.get("last_assistant_message"), 160) or message
        elif hook == "PermissionRequest":
            category = C.PERMISSION_NEEDED
            tool = optional_str(payload, "tool_name")
            if tool:
                meta["tool_name"] = tool[:80]
            message = f"Codex is asking for approval{f' to use {tool[:80]}' if tool else ''}."
        elif hook == "SessionStart":
            category = C.SESSION_RESUMED
            source = optional_str(payload, "source")
            if source:
                meta["source"] = source
        elif hook == "SessionEnd":
            category = C.SESSION_ENDED
        elif hook in ACTIVITY_EVENTS:
            category = C.SESSION_RESUMED
            meta["activity"] = hook
        elif hook in IGNORED_EVENTS:
            return None
        else:
            category = C.UNKNOWN

        dedup = f"codex:{session_id}:{hook}:{turn}" if session_id and turn else None
        if hook in ACTIVITY_EVENTS:
            dedup = None  # many activity events share a turn id
        return self._build(hook, category, session_id, payload, message, meta, dedup)

    def _translate_notify(self, payload: Mapping[str, Any], options: AdapterOptions) -> AgentEvent:
        session_id = optional_str(payload, "thread-id")
        turn = optional_str(payload, "turn-id")
        message = "Codex finished responding."
        if options.include_message_preview:
            message = sanitize_message(payload.get("last-assistant-message"), 160) or message
        dedup = f"codex:{session_id}:Stop:{turn}" if session_id and turn else None
        return self._build(
            "notify:agent-turn-complete",
            C.RESPONSE_STOPPED,
            session_id,
            payload,
            message,
            {},
            dedup,
        )

    def _build(
        self,
        native: str,
        category: EventCategory,
        session_id: str | None,
        payload: Mapping[str, Any],
        message: str | None,
        meta: dict[str, Any],
        dedup: str | None,
    ) -> AgentEvent:
        try:
            return AgentEvent(
                provider=self.provider_id,
                native_type=native,
                category=category,
                session_id=session_id,
                project_path=optional_str(payload, "cwd"),
                message=message,
                metadata=meta,
                dedup_key=dedup,
            )
        except ValueError as exc:
            raise AdapterError(f"payload failed validation: {exc.__class__.__name__}") from exc

    def hook_response(self, payload: Mapping[str, Any] | None) -> HookResponse:
        event = payload.get("hook_event_name") if isinstance(payload, Mapping) else None
        if event in JSON_STDOUT_EVENTS:
            return HookResponse(stdout="{}")
        return HookResponse()
