"""Example: integrating a hypothetical agent ("acme-agent") with AgentPing.

Suppose acme-agent can run a command on lifecycle events and pipes JSON like::

    {"event": "turn.done", "conversation": "c-42", "workdir": "C:/src/app"}
    {"event": "approval.requested", "conversation": "c-42", "tool": "shell"}
    {"event": "user.replied", "conversation": "c-42"}

Steps to ship a real integration (see docs/INTEGRATIONS.md, "Adding a provider"):

1. Write an adapter like the one below in ``src/agentping/adapters/acme/adapter.py``.
2. Register it in ``agentping/adapters/registry.py`` (``_BUILTIN``).
3. Add documented payload fixtures under ``tests/fixtures/acme/`` and contract tests.
4. If the agent's config can be edited safely, add an installer (subclass
   ``JsonHooksInstaller`` when its config uses the same ``hooks`` shape).
5. Document setup, limitations and what was verified against a real install.

No change to the router, policy, reminders or notification backends is needed.
Until then, a script can already reach AgentPing through the generic interface:

    agentping send --provider acme-agent --category RESPONSE_STOPPED --session c-42
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
)
from agentping.contracts.events import AgentEvent, EventCategory

_MAP = {
    "turn.done": EventCategory.RESPONSE_STOPPED,  # a response ended, not "task done"
    "approval.requested": EventCategory.PERMISSION_NEEDED,
    "user.replied": EventCategory.SESSION_RESUMED,  # resolves pending reminders
    "session.closed": EventCategory.SESSION_ENDED,
}


class AcmeAgentAdapter(ProviderAdapter):
    provider_id = "acme-agent"
    display_name = "Acme Agent"
    capabilities = ProviderCapabilities(
        categories=frozenset(_MAP.values()),
        # Declaring a resolution signal enables reminder escalation for this provider.
        resolution_signals=frozenset({EventCategory.SESSION_RESUMED}),
    )

    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        if not isinstance(payload, Mapping):
            raise AdapterError("payload must be a JSON object")
        native = optional_str(payload, "event")
        if native is None:
            raise AdapterError("missing or invalid required field 'event'")
        category = _MAP.get(native)
        if category is None:
            return None  # an event AgentPing does not care about
        meta: dict[str, Any] = {}
        tool = optional_str(payload, "tool")
        if tool:
            meta["tool_name"] = tool[:80]  # names only, never tool arguments
        try:
            return AgentEvent(
                provider=self.provider_id,
                native_type=native,
                category=category,
                session_id=optional_str(payload, "conversation"),
                project_path=optional_str(payload, "workdir"),
                metadata=meta,
            )
        except ValueError as exc:
            raise AdapterError(f"payload failed validation: {exc.__class__.__name__}") from exc
