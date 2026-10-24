# Architecture

TurnPing is a single-user desktop utility: one tray process plus a short-lived hook
process per agent event. There is no server, no network listener and no cloud component.

```text
 coding agent (Claude Code / Codex / Gemini CLI / script)
        |  runs a documented hook command, JSON on stdin
        v
 turnping hook <provider>         (short-lived process, ~0.4 s)
   read stdin (bounded) -> adapter.translate() -> AgentEvent (validated, redacted)
   + host window discovery (parent-process walk)
        |  authenticated named pipe (HMAC challenge, JSON only, 64 KiB cap)
        |  fallback: spool file + start the app detached, or headless delivery
        v
 TurnPing desktop app (tray)
   IPC server thread --> Engine thread (bounded queue)
                           EventRouter.process():
                             dedup -> session association -> reminder resolution
                             -> policy -> dispatch -> history
                           reminder ticks, spool drain, retention pruning
   Qt GUI thread: tray, dashboard, history, settings, popups (via queued signals)
        |
        v
 NotificationManager -> Windows toast (WinRT) | TurnPing popup | sound
```

## Components

| Module | Responsibility |
|---|---|
| `contracts/events.py` | `AgentEvent` (pydantic, schema v1), `EventCategory`, `Priority`, `HostWindow`. Validation, timestamp normalization, sanitization. |
| `adapters/` | One adapter per provider. Pure functions from native payload to `AgentEvent` or `None`. They declare `ProviderCapabilities` (which categories they can substantiate, and which resolve pending alerts). `registry.py` is the only place that lists providers. |
| `hook.py` | The hook entry point. Bounded stdin read, adapter translation, delivery over IPC/spool/headless, provider-specific stdout (`{}` for Gemini and Codex Stop). Always exits 0. |
| `ingest/ipc.py` | Named-pipe server/client with HMAC authentication, size caps, JSON framing (never pickle). Doubles as the single-instance lock. |
| `ingest/spool.py` | Bounded on-disk queue for events produced while the app is not running. |
| `core/router.py` | The pipeline. Every stage is wrapped so failures are logged, not raised. |
| `core/deduplication.py` | Event-id, provider-key (24 h) and short-window duplicate rules. |
| `core/session_manager.py` | Session state derived only from substantiated events. |
| `core/policy.py` | Pure decision function: notify / suppress / record, priority, sound. |
| `core/reminders.py` | In-memory escalation scheduler (one per session, bounded count). |
| `core/dispatcher.py` | Formats notifications (title, body, attribution, click URI). |
| `core/engine.py` | Background thread owning the router, ticks, spool drain and pruning. |
| `core/navigation.py`, `platform/windows.py` | Click-to-return: verified window focus, VS Code folder focus, fallback details. HKCU registry helpers. |
| `notifications/` | Backend interface, WinRT toast backend, manager with fallback order, sounds. |
| `storage/` | SQLite (WAL) with versioned migrations, corruption quarantine, and the repository (all SQL, parameterized). |
| `installation/` | Safe JSON hook installers, backups, validation, and per-user OS registration. |
| `desktop/` | PySide6 UI. `Controller` mediates. All blocking work runs on the engine thread or the thread pool. |
| `cli.py` | `turnping` commands. |

## Key decisions

- **Python 3.12+, PySide6, pydantic v2, SQLite, TOML**, as specified. Python 3.12 is the
  floor because the code uses 3.12 stdlib features (`shutil.rmtree(onexc=)`, PEP 701
  f-strings). Developed and tested on 3.14.6.
- **WinRT directly instead of a toast wrapper**, for full control of toast XML (protocol
  activation, `reminder` scenario for urgent alerts), reading the notifier `setting` to
  report *disabled* honestly, and confirming delivery through `ToastNotificationManager.history`.
- **Protocol activation for clicks** (`turnping://open?event=<id>`, registered per user).
  It works after the process that showed the toast has exited, with no COM server needed.
- **Own popup fallback.** On the development machine, Windows notifications were turned
  off for the user account. TurnPing detects this and uses its own topmost popup while
  the app runs, instead of failing silently.
- **Adapter runs inside the hook process.** Raw payloads (prompts, tool input,
  assistant text) never leave the hook. Only the normalized event crosses IPC. This
  costs about 100–150 ms of pydantic import per hook, acceptable for the event types used.
- **`multiprocessing.connection` named pipe with HMAC.** Stdlib on both sides,
  authentication before any data is read, `send_bytes/recv_bytes` only (no unpickling).
  Authentication runs on a worker thread so a stalled client cannot block accepts.
- **Async hooks for high-frequency events.** Claude `PostToolUse`/`UserPromptSubmit`/
  `SessionStart` and Codex background hooks never block the agent. Codex
  `PermissionRequest` is registered as a background hook, which Codex documents as unable
  to approve or deny anything.
- **No Claude `PermissionRequest` hook.** Claude's `Notification:permission_prompt` already
  has a "user is away" gate (about 6 s without typing) and cannot influence the decision.
  The adapter still understands `PermissionRequest` if a user adds it, and the policy
  coalesces duplicates.
- **Reminders are in memory.** Exiting cancels them, so a reminder can never outlive the
  situation it was about after a restart.
- **Stale async activity is ignored.** An activity event timestamped before the session's
  last update (async hooks can arrive late) cannot regress state or cancel a newer alert.
- **Configuration in TOML, state in SQLite.** User settings are hand-editable. Runtime
  state (pause flag, history, sessions) lives in the database.
- **Paths are resolved through Win32 APIs** (`SHGetKnownFolderPath`, `GetUserNameW`) when
  environment variables are missing, because Gemini CLI runs hooks with a sanitized env.

## Threading model

- **GUI thread:** widgets only. Data arrives through queued Qt signals.
- **Engine thread:** all event processing, reminders, spool, pruning (`Engine.call()` for
  serialized work).
- **IPC threads:** accept loop plus up to 8 short-lived client handlers. They only
  validate and enqueue.
- **Thread pool:** dashboard/history queries, installer actions, registry changes.

The SQLite connection is shared with a re-entrant lock (`check_same_thread=False`) and
uses WAL plus a busy timeout, so a concurrent headless hook process can also write.

## Extending

See [INTEGRATIONS.md](INTEGRATIONS.md#adding-a-provider) and
[examples/example_adapter.py](examples/example_adapter.py). A new provider needs an
adapter, a registry entry, fixtures and tests, plus optionally an installer. The router,
policy, reminders and notification backends do not change.
