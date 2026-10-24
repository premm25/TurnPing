# Security and privacy design

To report a vulnerability, see the root [SECURITY.md](../SECURITY.md).

## Threat model

TurnPing runs as the logged-in user and handles data written by coding agents (which
may contain secrets, code or attacker-influenced text) and by other local processes. Its
goals:

1. Never change an agent's behavior: no approvals, denials, blocking or injected context.
2. Never execute or interpret event content.
3. Never leak prompts, code, tool arguments or secrets to disk, logs, notifications or the network.
4. Never damage user configuration.

Out of scope: malware already running as the same user (it can read your files and
agent configs directly), and the security of the coding agents themselves.

## Controls

| Concern | Control |
|---|---|
| Changing agent decisions | Hooks print nothing (Claude), `{}` (Gemini, Codex Stop) and always exit 0. Codex `PermissionRequest` runs as a background hook, which Codex documents as unable to approve or deny. Claude `PermissionRequest` is not installed. Tests assert the exact stdout. |
| Code execution from payloads | Payloads are parsed as JSON and mapped field by field. Nothing is passed to a shell, `eval`, template, or HTML renderer (Qt labels are forced to `PlainText`; toast XML is escaped). Subprocesses use argument lists, never `shell=True`. |
| Data minimization | Adapters copy only routing fields (session id, cwd, event name, notification type, tool *name*, error type). `prompt`, `tool_input`, `tool_response`, transcripts and `last_assistant_message` are never stored. The optional preview (off by default) is redacted and truncated. |
| Redaction | All free text and every log line pass through `security/redaction.py` (API keys, GitHub/Slack/AWS/Google tokens, JWTs, private keys, `password=`/`token:`/`Bearer` patterns). Hook failures log the payload *shape* only. |
| Local IPC | Named pipe only (no TCP/UDP listener). HMAC challenge/response in both directions using a 32-byte random key in the user profile. Authentication happens before any data is read. JSON over `send_bytes`/`recv_bytes`; pickle-based `recv()` is never used. 64 KiB message cap, 8 concurrent clients, 2 s read timeout. |
| Input bounds | Hook stdin capped at 8 MiB (oversized payloads keep only routing fields). Metadata ≤ 16 scalar keys. Strings length-limited. Bounded engine queue (1000), spool (200 files of ≤ 16 KiB, stale after 6 h) and history (retention + row cap). |
| Click-to-return | `turnping://open?event=<id>` accepts exactly one strictly validated id and nothing else. The focused window is verified by process id so a recycled window handle is never focused. |
| Config edits | Parse-or-refuse, backup, preserve unrelated content, atomic write, concurrent-change detection, verify-or-restore. |
| Privileges | Per-user only (HKCU, `%LOCALAPPDATA%`). No elevation, services or drivers. |
| Telemetry | None. The only network activity is whatever the agents themselves do. |

## Stored data

See the README "What data is stored" section. Windows also keeps delivered toasts in the
Action Center (an OS database). This is why message previews are opt-in.

## Known limitations

- Any process running as the same user can read `ipc.key` and send events, so it could
  show fake notifications. It can't execute code through TurnPing. Same-user isolation
  is not a Windows security boundary.
- Window titles of the hosting terminal/editor are stored for click-to-return. They can
  include a file or folder name.
- Unsigned binaries: verify checksums or build from source.
