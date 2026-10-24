# Roadmap

## Naming

"AgentPing" is a **provisional** name. As of 2026-10-24:

- PyPI `agentping` is taken (an MCP server bridging agents to an "AgentPing" service), so
  this package is named `agentping-desktop`, but the import name `agentping` would still
  clash if both were installed.
- GitHub has several projects with this name, including
  [ericermerimen/agentping](https://github.com/ericermerimen/agentping), a macOS menu-bar
  app for Claude Code sessions in the same space, and an `agent-ping` organization with
  Claude Code integrations.
- `agentping.dev` / `agentping.com` could not be checked from the build environment.

**Recommendation:** pick a distinct name before any public release or package upload,
then rename the package, the `APP_ID` (toast identity) and the URI scheme.

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
