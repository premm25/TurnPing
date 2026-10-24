# Troubleshooting

Always start with:

```powershell
agentping doctor          # every check reports what it observed, with a fix
agentping integrations
agentping test-notification
```

Logs: `%LOCALAPPDATA%\AgentPing\logs\` (`agentping.log` app, `hook.log` hooks,
`hook-failures.jsonl` rejected payloads, by shape only).

## No notification appears

| Symptom (`test-notification` / doctor) | Cause and fix |
|---|---|
| `delivery: unavailable` / "Windows notifications are turned off for your Windows account" | Settings → System → Notifications → turn **Notifications** on. Until then AgentPing shows its own popups **while the app is running** (`agentping run`). |
| "disabled for AgentPing" | Settings → System → Notifications → find AgentPing → On. |
| `delivery: dispatched` but nothing visible | Windows accepted the toast but didn't confirm display. Check Focus / Do Not Disturb, and the Action Center (Win+N). |
| `delivery: confirmed` but nothing seen | It's in the Action Center. Focus mode probably suppressed the banner. |
| Test works, agent events don't | `agentping integrations`: if **Not installed**, run `agentping install <agent>`. If **Misconfigured**, the hook points to a moved or deleted AgentPing, so re-run install. For Codex, trust the hooks with `/hooks`. Then check `hook-failures.jsonl`. |
| Events appear in History with `suppressed` | The reason is shown (paused, quiet hours, event type disabled, duplicate, "already alerted for this pending request"). |

## Clicking a notification doesn't return to my session

AgentPing focuses the terminal/editor **window** captured when the event fired. It
cannot select a specific tab or split pane. If focusing isn't possible (window closed,
Windows foreground restrictions, an unknown terminal), AgentPing shows a **Return to
session** dialog with the project path, session id and window name, plus buttons to open
the folder or VS Code. For VS Code, AgentPing can also ask `code <folder>` to focus the
right window. If several VS Code windows exist, AgentPing picks the one whose title
contains the project folder name.

## Reminders

Reminders only fire for permission/input alerts, only while the app runs, at most
`max_reminders` times, and stop on any session activity, when the response finishes or
the session ends, or when you open/dismiss the alert. Gemini CLI has no reminders by
design. If a reminder fired although you had answered, the agent's resolution hook
didn't run (check `PostToolUse`/`UserPromptSubmit` are installed: `agentping integrations`).

## Installation refuses to edit my settings

"`settings.json` is not plain JSON": the file contains comments or trailing commas.
AgentPing won't rewrite it. Remove the comments, or add the hook entries by hand (see
[INTEGRATIONS.md](INTEGRATIONS.md)). "changed while AgentPing was editing it": another
program saved the file at the same moment, so retry.

## Restoring agent settings

Every edit leaves a backup in `%LOCALAPPDATA%\AgentPing\backups\<provider>\`. To restore:
`Copy-Item <backup> $HOME\.claude\settings.json`.

## Database problems

If the database is unreadable, AgentPing moves it to `agentping.corrupt-<time>.db`
(never deletes it), starts fresh, and shows a banner. Inspect the old file with any
SQLite tool.

## The app doesn't start / "already running"

Only one instance runs per data folder. Starting another just opens the existing
dashboard. `agentping quit` stops it. If the tray icon is hidden, check the taskbar's
hidden-icons area.

## Slow hooks

Each hook run starts Python (about 0.3–0.5 s). Frequent events (`PostToolUse`,
`UserPromptSubmit`) run asynchronously and never delay the agent. If an agent reports
a hook timeout, check antivirus exclusions for the AgentPing folder.
