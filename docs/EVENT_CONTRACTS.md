# Event contracts

## AgentEvent (schema version 1)

Defined in `src/agentping/contracts/events.py`. Unknown fields are rejected.

| Field | Type | Rules |
|---|---|---|
| `schema_version` | `1` | Only `1` is accepted. Future versions add fields compatibly or bump this. |
| `event_id` | string | 8–64 chars `[A-Za-z0-9_-]`. Generated (uuid4 hex) if omitted. |
| `provider` | string | Lowercase id, `^[a-z][a-z0-9]*([-_][a-z0-9]+)*$`, ≤ 40 chars. |
| `native_type` | string | The provider's own event name (e.g. `Stop`, `Notification:permission_prompt`), ≤ 80 chars, restricted charset. |
| `category` | enum | See below. |
| `timestamp` | datetime | ISO-8601 (`Z` accepted), epoch seconds or milliseconds. Naive values are treated as UTC. Always stored timezone-aware in UTC. Defaults to now. |
| `session_id` | string? | ≤ 200 printable chars. |
| `project_path` | string? | ≤ 1024 printable chars. |
| `message`, `title` | string? | Single line, control characters stripped, secrets redacted, ≤ 240 chars. |
| `metadata` | object | ≤ 16 flat keys `[A-Za-z0-9_.-]`, scalar values (strings ≤ 200 chars). Nested values are dropped. |
| `dedup_key` | string? | Stable identity of the provider-side event, if the provider has one. |
| `host` | object? | Window info for click-to-return (`hwnd`, `pid`, `process_name`, `title`, `terminal`). |

Derived: `session_key` = `provider:session_id`. Without a session id, it falls back to
`provider:path:<normalized project path>`, which cannot tell apart two sessions in the
same folder. The UI says so when this happens.

## Categories

| Category | Meaning | Default |
|---|---|---|
| `RESPONSE_STOPPED` | The agent finished a response/turn and is waiting. **Not** "task done". | Notify, normal, sound |
| `TASK_COMPLETED` | A source with real task semantics reported completion (generic integrations). No built-in agent emits this. | Notify, normal, sound |
| `INPUT_NEEDED` | The agent is waiting for an answer or input. | Notify, high, sound, reminders |
| `PERMISSION_NEEDED` | The agent is blocked on an approval. | Notify, urgent, sound, reminders |
| `TASK_FAILED` | A failure was reported (e.g. a turn ended by an API error). | Notify, high, sound |
| `SESSION_RESUMED` | Activity: prompt submitted, tool ran, session (re)started. Resolves reminders. | Record only |
| `SESSION_ENDED` | The session exited. Cancels reminders. | Record only |
| `UNKNOWN` | A recognized payload with an event AgentPing doesn't model yet. | Record only |

## Processing guarantees

1. **Validation:** malformed payloads raise `AdapterError` in the hook. They are logged by
   *shape only* (key → type) to `logs/hook-failures.jsonl` and never crash anything.
2. **Duplicates:** same `event_id` → dropped. Same `dedup_key` within 24 h → dropped.
   With no key, same session + native type + message within `dedup.window_seconds`
   (default 10 s) → dropped. Anything else is a legitimate repeat.
3. **Coalescing:** if a session is already waiting on the same thing (no activity since),
   a second permission/input alert is recorded but not shown.
4. **Activity events** update session state but are not written to history (noise).

## Generic ingestion

Any local program can send events with the CLI. Both forms go through the generic
adapter's validation:

```powershell
agentping send --provider my-build --category TASK_COMPLETED --message "Build finished" `
  --project C:\src\app --session build-42 --event-id build42done

'{"provider":"my-build","category":"TASK_FAILED","message":"Tests failed"}' | agentping send --json
```

Generic payload keys: `provider` (required, must not be a built-in id), `category`
(required), `native_type`, `session_id`, `project_path`, `message`, `title`, `event_id`
(also used as the dedup key), `dedup_key`, `timestamp`, `metadata`, `schema_version`.
Unknown keys are rejected. Payloads are capped at 64 KiB. Values are data only: nothing
is executed, rendered as HTML, or interpreted as configuration.

To have reminders resolve, send a `SESSION_RESUMED` event with the same `session_id`
once the human has responded.

## IPC protocol (internal)

The named pipe `\\.\pipe\AgentPing-<hash(user, data dir)>` carries JSON messages after
an HMAC challenge in both directions, keyed by `ipc.key`:

- `{"v":1,"type":"event","event":{...}}`
- `{"v":1,"type":"test_notification","event":{...}}` (processed synchronously, result returned)
- `{"v":1,"type":"command","command":"ping|show|open_event|acknowledge|pause|resume|quit","event_id":"..."}`

This protocol is internal and may change between versions. Integrations should use
`agentping send`.
