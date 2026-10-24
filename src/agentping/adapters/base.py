"""The public adapter contract.

An adapter turns one provider's native hook payload into an :class:`AgentEvent`.
Adapters are pure: no I/O, no side effects, no network. They must never raise on
unexpected input; instead raise :class:`AdapterError` (malformed) or return ``None``
(valid but irrelevant event, which is deliberately ignored).

To add a provider, subclass :class:`ProviderAdapter`, register it in
``agentping.adapters.registry``, add fixtures under ``tests/fixtures/<provider>/`` and
document it in ``docs/INTEGRATIONS.md``. The core does not need to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from agentping.contracts.events import AgentEvent, EventCategory


class AdapterError(ValueError):
    """The payload could not be translated (malformed or missing required fields)."""


@dataclass(frozen=True)
class AdapterOptions:
    """User preferences an adapter may consult while translating."""

    include_message_preview: bool = False


@dataclass(frozen=True)
class HookResponse:
    """What the hook process must print/return to satisfy the provider's contract."""

    stdout: str = ""
    exit_code: int = 0


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider's integration can reliably substantiate.

    ``resolution_signals`` lists categories that tell AgentPing the human acted (e.g.
    the session resumed). Reminder escalation is only enabled for providers that have
    at least one resolution signal; without one, reminders could fire after the user
    already responded.
    """

    categories: frozenset[EventCategory]
    resolution_signals: frozenset[EventCategory] = field(default_factory=frozenset)

    @property
    def supports_escalation(self) -> bool:
        return bool(self.resolution_signals)


class ProviderAdapter(ABC):
    provider_id: ClassVar[str]
    display_name: ClassVar[str]
    capabilities: ClassVar[ProviderCapabilities]

    @abstractmethod
    def translate(
        self, payload: Mapping[str, Any], options: AdapterOptions | None = None
    ) -> AgentEvent | None:
        """Translate a native payload. Return None for events AgentPing ignores."""

    def hook_response(self, payload: Mapping[str, Any] | None) -> HookResponse:
        """Output the hook must produce so the provider continues unaffected.

        Defaults to printing nothing and exiting 0, which every supported provider
        treats as "no decision". Never return anything that approves, denies, blocks
        or alters the agent's behaviour.
        """
        return HookResponse()


def require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"missing or invalid required field '{key}'")
    return value


def optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None
