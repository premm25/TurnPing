# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-24

First alpha release.

### Added
- Provider-independent event contract (schema v1) with validation, redaction and UTC timestamps.
- Adapters: Claude Code (verified against Claude Code 2.1.309), Codex CLI and Gemini CLI
  (contract-tested, not yet verified against real installs), and a generic adapter.
- Hook entry point that never alters agent behavior, with an authenticated named-pipe
  transport, a bounded spool, auto-start of the app, and a headless fallback.
- Event pipeline: deduplication, session tracking, policy (priorities, sounds, quiet
  hours, pause, per-agent and per-event rules), coalescing, and bounded reminder escalation.
- Native Windows toasts (WinRT) with honest delivery status, a TurnPing popup
  fallback when Windows notifications are off, and click-to-return via `turnping://`.
- Tray app: dashboard, searchable history, settings, session-details fallback, light/dark theme.
- Safe, idempotent, reversible hook installers with backups and verification.
- CLI: `run`, `doctor`, `test-notification`, `integrations`, `install`, `uninstall`,
  `history`, `send`, `pause`, `resume`, `quit`.
- PyInstaller build (two executables), install/uninstall scripts, and GitHub Actions CI.
- Per-user Windows installer (`TurnPing-<version>-Setup.exe`, Inno Setup) with an
  Apps & features uninstall entry.
