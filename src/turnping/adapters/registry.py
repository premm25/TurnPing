"""Adapter registry: the only place that knows which providers exist."""

from __future__ import annotations

from turnping.adapters.base import ProviderAdapter
from turnping.adapters.claude_code import ClaudeCodeAdapter
from turnping.adapters.codex import CodexAdapter
from turnping.adapters.gemini_cli import GeminiCliAdapter
from turnping.adapters.generic import GenericAdapter

_BUILTIN: dict[str, type[ProviderAdapter]] = {
    ClaudeCodeAdapter.provider_id: ClaudeCodeAdapter,
    CodexAdapter.provider_id: CodexAdapter,
    GeminiCliAdapter.provider_id: GeminiCliAdapter,
}

TEST_PROVIDER_ID = "turnping"  # used by `turnping test-notification`
BUILTIN_PROVIDER_IDS = frozenset(_BUILTIN)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {pid: cls() for pid, cls in _BUILTIN.items()}
        self._adapters[GenericAdapter.provider_id] = GenericAdapter(
            reserved_provider_ids=BUILTIN_PROVIDER_IDS
        )

    def register(self, adapter: ProviderAdapter) -> None:
        if adapter.provider_id in self._adapters:
            raise ValueError(f"adapter '{adapter.provider_id}' is already registered")
        self._adapters[adapter.provider_id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter | None:
        return self._adapters.get(provider_id)

    def all(self) -> list[ProviderAdapter]:
        return list(self._adapters.values())

    def display_name(self, provider_id: str) -> str:
        if provider_id == TEST_PROVIDER_ID:
            return "TurnPing"
        adapter = self._adapters.get(provider_id)
        if adapter is not None and adapter.provider_id != GenericAdapter.provider_id:
            return adapter.display_name
        return provider_id


_default: AdapterRegistry | None = None


def default_registry() -> AdapterRegistry:
    global _default
    if _default is None:
        _default = AdapterRegistry()
    return _default
