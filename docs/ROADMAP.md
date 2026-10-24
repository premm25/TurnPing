# Roadmap

## Naming

The project was renamed from "AgentPing" to **TurnPing** before its first public release,
because "AgentPing" was already used on PyPI and by similar projects on GitHub. As of
2026-10-24, `turnping` was free on PyPI and no GitHub repositories used the name.
Trademarks and domains have not been checked.

## Near term

- Verify Codex CLI and Gemini CLI against real installations; record results in INTEGRATIONS.md.
- Real interactive permission-prompt verification for Claude Code.
- Signed release builds and an MSIX or Inno Setup installer.
- Windows Terminal tab-level focus, if a supported API emerges (today: window-level only).
- Optional "still running" heartbeat for long tasks, only where a provider exposes it.

## Later

- macOS and Linux backends (notifications, window focus, startup) behind the existing interfaces.
- More agents via their documented hook systems (Cursor, Copilot CLI, ...).
- Per-project rules (e.g. mute a repository).

## Non-goals

LLM-based event detection, terminal scraping, cloud relays, telemetry, and anything that
acts on an agent's behalf.
