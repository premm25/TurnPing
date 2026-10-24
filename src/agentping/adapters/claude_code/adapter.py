"""Claude Code hook adapter.

Source of truth: https://code.claude.com/docs/en/hooks (checked 2026-10-24 against
Claude Code 2.1.309). Every command hook receives a JSON object on stdin with
``session_id``, ``cwd``, ``hook_event_name`` and event-specific fields.

Semantics we rely on:

* ``Stop`` fires when the main agent finishes *responding*. It does not mean the
  user's overall task is done, so it maps to RESPONSE_STOPPED, never TASK_COMPLETED.
* ``StopFailure`` fires instead of Stop when the turn ends due to an API error.
* ``Notification`` with ``notification_type`` ``permission_prompt`` fires when a
  permission prompt has waited ~6 s without the user typing (Claude's own "away" gate).
* ``PermissionRequest`` fires immediately when a permission dialog is about to show.
* ``UserPromptSubmit`` / ``PostToolUse`` prove the session moved on, which resolves
  pending permission/input alerts.

The hook never prints output for Claude Code, so it cannot approve, deny or block.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentping.adapters.base import (
    AdapterError,
    AdapterOptions,
    ProviderAdapter,
    ProviderCapabilities,
    optional_str,
    require_str,
)
from agentping.contracts.events import AgentEvent, EventCategory
from agentping.security.redaction import sanitize_message

C = EventCategory

# notification_type -> category. Types not listed are ignored (return None).
NOTIFICATION_TYPES: dict[str, EventCategory] = {
    "permission_prompt": C.PERMISSION_NEEDED,
    "elicitation_dialog": C.INPUT_NEEDED,
    "elicitation_url_dialog": C.INPUT_NEEDED,
    "agent_needs_input": C.INPUT_NEEDED,
    "idle_prompt": C.INPUT_NEEDED,
    "quota_auto_resume_stale": C.INPUT_NEEDED,
    "quota_auto_resume_disabled": C.INPUT_NEEDED,
    "agent_completed": C.RESPONSE_STOPPED,
    "quota_auto_resume_fired": C.SESSION_RESUMED,
    "elicitation_complete": C.SESSION_RESUMED,
    "elicitation_response": C.SESSION_RESUMED,
}

# Events that only prove the session is active again.
ACTIVITY_EVENTS = frozenset(
    {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "PreToolUse"}
)

IGNORED_EVENTS = frozenset(
    {
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
        "InstructionsLoaded",
        "ConfigChange",
        "CwdChanged",
        "FileChanged",
        "MessageDisplay",
        "Setup",
        "PreModelSwitch",
        "PostModelSwitch",
    }
)

_DEFAULT_MESSAGES = {
    C.RESPONSE_STOPPED: "Claude finished responding.",
    C.PERMISSION_NEEDED: "Claude needs your permission.",
    C.INPUT_NEEDED: "Claude is waiting for your input.",
}


class ClaudeCodeAdapter(ProviderAdapter):
    provider_id = "claude-code"
    display_name = "Claude Code"
    capabilities = ProviderCapabilities(
        categories=frozenset(
            {
                C.RESPONSE_STOPPED,
                C.INPUT_NEEDED,
                C.PERMISSION_NEEDED,
                C.TASK_FAILED,
                C.SESSION_RESUMED,
                C.SESSION_ENDED,
            }
        ),
        resolution_signals=frozenset({C.SESSION_RESUMED, C.RESPONSE_STOPPED}),
    )

    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        options = options or AdapterOptions()
        if not isinstance(payload, Mapping):
            raise AdapterError("payload must be a JSON object")
        hook = require_str(payload, "hook_event_name")
        session_id = optional_str(payload, "session_id")
        cwd = optional_str(payload, "cwd")
        turn = optional_str(payload, "prompt_id")
        meta: dict[str, Any] = {}
        mode = optional_str(payload, "permission_mode")
        if mode:
            meta["permission_mode"] = mode
        if optional_str(payload, "agent_id"):
            # A subagent context; its stop/notifications belong to the parent session.
            meta["subagent"] = True

        category: EventCategory
        message: str | None = None
        title: str | None = None
        native = hook
        dedup_parts: list[str] = [hook]

        if hook == "Stop":
            category = C.RESPONSE_STOPPED
            tasks = payload.get("background_tasks")
            if isinstance(tasks, list) and tasks:
                meta["background_tasks"] = len(tasks)
            if options.include_message_preview:
                message = sanitize_message(payload.get("last_assistant_message"), limit=160)
            dedup_parts.append(turn or "")
        elif hook == "StopFailure":
            category = C.TASK_FAILED
            error = optional_str(payload, "error") or "unknown"
            meta["error"] = error
            message = f"The turn ended with an API error ({error})."
            dedup_parts += [turn or "", error]
        elif hook == "Notification":
            ntype = optional_str(payload, "notification_type") or ""
            native = f"Notification:{ntype}" if ntype else "Notification"
            mapped = NOTIFICATION_TYPES.get(ntype)
            if mapped is None:
                return None
            category = mapped
            meta["notification_type"] = ntype
            if ntype == "idle_prompt":
                meta["reason"] = "idle"
            message = sanitize_message(payload.get("message"))
            title = sanitize_message(payload.get("title"), limit=80)
            dedup_parts += [ntype, turn or "", message or ""]
        elif hook == "PermissionRequest":
            category = C.PERMISSION_NEEDED
            tool = optional_str(payload, "tool_name")
            if tool:
                meta["tool_name"] = tool[:80]
                message = f"Claude wants to use {tool[:80]}."
            # tool_input is deliberately never copied: it can contain commands/secrets.
            dedup_parts += [turn or "", tool or ""]
        elif hook == "SessionStart":
            category = C.SESSION_RESUMED
            source = optional_str(payload, "source")
            if source:
                meta["source"] = source
            dedup_parts.append(source or "")
        elif hook == "SessionEnd":
            category = C.SESSION_ENDED
            reason = optional_str(payload, "reason")
            if reason:
                meta["reason"] = reason
        elif hook in ACTIVITY_EVENTS:
            category = C.SESSION_RESUMED
            meta["activity"] = hook
            tool_use = optional_str(payload, "tool_use_id")
            dedup_parts += [turn or "", tool_use or ""]
        elif hook in IGNORED_EVENTS:
            return None
        else:
            category = C.UNKNOWN

        if category in _DEFAULT_MESSAGES and not message:
            message = _DEFAULT_MESSAGES[category]

        # Only build a stable dedup key when the source gave us a per-turn identifier;
        # otherwise the core falls back to a short time window.
        has_identity = bool(session_id and (turn or hook in ("SessionEnd",)))
        dedup_key = (
            ":".join([self.provider_id, session_id or "", *dedup_parts]) if has_identity else None
        )

        try:
            return AgentEvent(
                provider=self.provider_id,
                native_type=native,
                category=category,
                session_id=session_id,
                project_path=cwd,
                message=message,
                title=title,
                metadata=meta,
                dedup_key=dedup_key,
            )
        except ValueError as exc:  # pydantic ValidationError subclasses ValueError
            raise AdapterError(f"payload failed validation: {exc.__class__.__name__}") from exc
