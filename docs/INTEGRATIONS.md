# Integrations

TurnPing uses only documented hook mechanisms. It never scrapes terminal output, never
returns a decision to the agent, and never approves or denies anything.

| | Claude Code | Codex CLI | Gemini CLI | Generic |
|---|---|---|---|---|
| Config file | `~/.claude/settings.json` | `~/.codex/hooks.json` | `~/.gemini/settings.json` | n/a |
| Automatic install | yes | yes | yes (plain-JSON files only) | n/a |
| Response finished | `Stop` | `Stop` | `AfterAgent` | `RESPONSE_STOPPED` |
| Permission needed | `Notification:permission_prompt` | `PermissionRequest` (background) | `Notification:ToolPermission` | `PERMISSION_NEEDED` |
| Input needed | `Notification:elicitation_*`, `agent_needs_input`, `quota_auto_resume_stale/disabled` (+ `idle_prompt` opt-in) | not exposed | not exposed | `INPUT_NEEDED` |
| Failure | `StopFailure` | not exposed | not exposed | `TASK_FAILED` |
| Resolution signal | `UserPromptSubmit`, `PostToolUse`, `Stop`, `elicitation_response` | `UserPromptSubmit`, `PostToolUse`, `Stop`, `Interrupt` | none installed (see below) | `SESSION_RESUMED` |
| Reminders | yes | yes | **disabled** | yes |
| Verified against real install | **yes** (2.1.309) | no (not installed) | no (not installed) | yes |

Check status at any time with `turnping integrations` or the dashboard. Labels:

- **Installed and operational**: hooks are present, point to this TurnPing, and an event
  from the agent has been received.
- **Installed but unverified**: hooks are correct, but no event has arrived yet.
- **Not installed**: no TurnPing hooks were found (or the agent isn't on this machine).
- **Misconfigured**: invalid config file, missing hooks, or hooks pointing to a moved or
  deleted TurnPing. Fix with `turnping install <provider>`.

## Claude Code

Source: <https://code.claude.com/docs/en/hooks>, checked 2026-10-24.

`turnping install claude-code` adds these handlers to `~/.claude/settings.json`:

| Event | Mode | Why |
|---|---|---|
| `Stop` | sync, 10 s timeout | Response finished. Sync so `claude -p` runs don't cancel it. |
| `StopFailure` | sync | Turn ended by an API error (`rate_limit`, `billing_error`, ...). |
| `Notification` (matcher: `permission_prompt\|elicitation_dialog\|elicitation_url_dialog\|agent_needs_input\|quota_auto_resume_*\|elicitation_response`) | sync | Attention requests. Claude only sends `permission_prompt` after you've been away ~6 s, so you aren't pinged while watching. |
| `SessionStart` | async | Session start/resume (activity). |
| `SessionEnd` | 5 s timeout | Cancels reminders, marks the session ended. |
| `UserPromptSubmit` | async | You answered, so reminders stop. |
| `PostToolUse` (`*`) | async | A tool ran (e.g. after approval), so reminders stop. |

The hook prints nothing and exits 0, which Claude Code treats as "no decision".
`PermissionRequest` is deliberately not installed (see ARCHITECTURE). `idle_prompt`
(about 60 s after a response) is opt-in under Settings → Agents.

Verified on this machine (Windows 11 build 26200, Claude Code 2.1.309):
real `claude -p` sessions delivered `SessionStart`, `Stop` and `SessionEnd` through the
dev and the frozen PyInstaller build; three concurrent sessions in one folder were kept
separate; the hook captured the host VS Code window and click-to-return focused it.
Not exercised with a real interactive permission prompt; that path is covered by
documented-payload contract tests and end-to-end hook subprocess tests.

## Codex CLI

Source: <https://developers.openai.com/codex/hooks>, checked 2026-10-24.

`turnping install codex` writes `~/.codex/hooks.json` (it never edits `config.toml`):
`Stop` (sync, prints `{}` because Codex requires JSON for Stop), `PermissionRequest`
(background: Codex documents that background hooks cannot approve or deny), plus
`SessionStart`, `SessionEnd`, `UserPromptSubmit` and `PostToolUse`.

**After installing, open Codex and run `/hooks` to review and trust the new hooks.**
Codex skips untrusted hooks.

The adapter also understands the legacy `notify` program payload
(`agent-turn-complete`) if you configure `notify = ["<turnping-cli>", "hook", "codex"]`
yourself. Codex passes the JSON as the last argument.

Status: implemented and contract-tested. Not verified against a real Codex install.
How Codex launches hook commands on Windows (cmd or PowerShell) was not verified either.
TurnPing writes unquoted forward-slash paths, which work in both, as long as the install
path has no spaces.

## Gemini CLI

Source: <https://github.com/google-gemini/gemini-cli/tree/main/docs/hooks>, checked 2026-10-24.

`turnping install gemini-cli` adds `AfterAgent`, `Notification`, `SessionStart`,
`SessionEnd` and `BeforeAgent` to `~/.gemini/settings.json` (timeouts in milliseconds).
The hook always prints `{}` because Gemini requires stdout to be pure JSON. Gemini runs
hooks with a sanitized environment, so TurnPing resolves its data folder through
Windows APIs rather than environment variables.

**Reminders are disabled for Gemini CLI.** Gemini hooks are synchronous only, so the
per-tool `AfterTool` hook that would prove "permission granted, agent working again"
would add about 0.4 s to every tool call. Without it, TurnPing can't tell "still
blocked" from "already answered", so it doesn't escalate.

If your `settings.json` contains comments (Gemini tolerates them), TurnPing refuses to
rewrite it and leaves it untouched. Add this snippet manually instead (adjust the path):

```json
{
  "hooks": {
    "AfterAgent":   [{"hooks": [{"type": "command", "name": "turnping-after-agent", "timeout": 10000,
                                 "command": "C:/Users/you/AppData/Local/Programs/TurnPing/app/turnping-cli.exe hook gemini-cli"}]}],
    "Notification": [{"hooks": [{"type": "command", "name": "turnping-notification", "timeout": 10000,
                                 "command": "C:/Users/you/AppData/Local/Programs/TurnPing/app/turnping-cli.exe hook gemini-cli"}]}]
  }
}
```

Status: implemented and contract-tested. Not verified against a real Gemini CLI install.

## Generic / custom scripts

See [EVENT_CONTRACTS.md](EVENT_CONTRACTS.md#generic-ingestion). Example: notify when a
long build finishes.

```powershell
npm run build; if ($?) { turnping send --provider build --category TASK_COMPLETED --message "Build OK" }
             else      { turnping send --provider build --category TASK_FAILED --message "Build failed" }
```

## Adding a provider

1. Read the agent's **official** hook documentation and save representative payloads as
   fixtures in `tests/fixtures/<provider>/`.
2. Implement `ProviderAdapter` (see [examples/example_adapter.py](examples/example_adapter.py)).
   Map only what the source substantiates, never copy prompts or tool input, and declare
   `resolution_signals` only if they are reliable.
3. Register it in `adapters/registry.py`.
4. Add an installer if its config can be edited safely (`JsonHooksInstaller` covers the
   common `hooks` shape) and override `hook_response` if the agent needs specific stdout.
5. Add contract tests and document what was verified for real versus with fixtures.
