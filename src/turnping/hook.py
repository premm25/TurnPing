"""Hook entry point: ``turnping hook <provider>``.

Contract with the calling agent:
* Reads the native JSON payload from stdin (or, for Codex's legacy ``notify``, from the
  last command-line argument).
* Always exits 0 and prints only what the provider requires (``{}`` for Gemini CLI and
  for Codex's Stop, nothing for Claude Code), so the agent's behaviour is never changed:
  no approvals, no denials, no blocking, no injected context.
* Never prints diagnostics to stdout; failures go to ``logs/hook-failures.jsonl``.
* Returns quickly: it hands the normalized event to the running app over IPC. If the
  app is not running it spools the event and starts the app in the background (or, if
  that is disabled, delivers the notification itself without reminders).

Raw payloads never leave this process: only the normalized, redacted AgentEvent does.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any, BinaryIO

from turnping.adapters.base import AdapterError, AdapterOptions, HookResponse, ProviderAdapter
from turnping.security.paths import AppPaths, get_paths
from turnping.security.redaction import describe_payload_shape

log = logging.getLogger("turnping.hook")

MAX_STDIN_BYTES = 8 * 1024 * 1024
MAX_FAILURE_LINES = 200
LAUNCH_LOCK_SECONDS = 20.0

_FIELD_RE = {
    key: re.compile(rb'"' + key.encode() + rb'"\s*:\s*"((?:[^"\\]|\\.){0,1024})"')
    for key in ("hook_event_name", "session_id", "cwd", "prompt_id", "turn_id")
}


def read_stdin(stream: BinaryIO | None) -> bytes:
    """Read stdin, keeping at most MAX_STDIN_BYTES and draining the rest."""
    if stream is None:
        return b""
    kept = bytearray()
    while True:
        chunk = stream.read(1 << 16)
        if not chunk:
            break
        if len(kept) < MAX_STDIN_BYTES + 1:
            kept += chunk[: MAX_STDIN_BYTES + 1 - len(kept)]
    return bytes(kept)


def parse_payload(raw: bytes) -> dict[str, Any]:
    """Parse the payload; for oversized payloads recover only routing fields."""
    if len(raw) > MAX_STDIN_BYTES:
        # Large tool results (PostToolUse) can exceed the cap. We only need routing
        # fields from them, which appear as plain top-level strings.
        recovered: dict[str, Any] = {}
        for key, pattern in _FIELD_RE.items():
            match = pattern.search(raw)
            if match:
                with contextlib.suppress(Exception):
                    recovered[key] = json.loads(b'"' + match.group(1) + b'"')
        if "hook_event_name" not in recovered:
            raise AdapterError("payload exceeds size limit")
        return recovered
    if not raw.strip():
        raise AdapterError("empty payload")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AdapterError(f"payload is not valid JSON (line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise AdapterError("payload must be a JSON object")
    return data


def record_failure(
    paths: AppPaths, provider: str, reason: str, shape: dict[str, str] | None
) -> None:
    try:
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        path = paths.hook_failures_file
        entry = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": provider,
            "reason": reason[:300],
            "payload_shape": shape or {},
        }
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines = [*lines[-(MAX_FAILURE_LINES - 1) :], json.dumps(entry)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: S110 - diagnostics must never break the hook
        pass


def _launch_app(paths: AppPaths) -> bool:
    lock = paths.launch_lock_file
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime < LAUNCH_LOCK_SECONDS:
            return True  # another hook is already starting the app
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return _launch_app_with_args(["run", "--background"])


def _launch_app_with_args(args: list[str]) -> bool:
    """Start the desktop app detached from the calling agent's process tree."""
    from turnping.launcher import gui_command

    argv = [*gui_command(), *args]
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        attempts = [flags | CREATE_BREAKAWAY_FROM_JOB, flags]
    else:
        attempts = [0]
    for creationflags in attempts:
        try:
            subprocess.Popen(  # argument list, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=sys.platform != "win32",
            )
            return True
        except OSError:
            continue
    return False


def deliver(event: Any, paths: AppPaths, launch_app: bool) -> str:
    """Hand the event to the app; returns how it was delivered (for tests/diagnostics)."""
    from turnping.ingest.ipc import send_message
    from turnping.ingest.spool import Spool

    try:
        response = send_message({"type": "event", "event": event.to_wire()})
        if response.get("ok"):
            return "ipc"
        log.warning("app rejected event: %s", response.get("error"))
        return "rejected"
    except (ConnectionError, OSError, ValueError):
        pass
    if launch_app and Spool(paths.spool_dir).put(event) is not None and _launch_app(paths):
        return "spooled"
    # Headless fallback: deliver directly (no reminders; the app is not running).
    from turnping.runtime import build_runtime

    runtime = build_runtime(paths=paths)
    try:
        runtime.router.process(event)
    finally:
        runtime.close()
    return "headless"


def run_hook(
    provider: str,
    *,
    stdin: BinaryIO | None,
    argv_payload: str | None = None,
    paths: AppPaths | None = None,
    capture_host: bool = True,
) -> tuple[HookResponse, str]:
    from turnping.adapters.registry import default_registry
    from turnping.config import load_config

    paths = paths or get_paths()
    adapter: ProviderAdapter | None = default_registry().get(provider)
    payload: dict[str, Any] | None = None
    if adapter is None:
        record_failure(paths, provider, "unknown provider id", None)
        return HookResponse(), "unknown-provider"
    try:
        raw = argv_payload.encode("utf-8") if argv_payload else read_stdin(stdin)
        payload = parse_payload(raw)
        config = load_config(paths.config_file).config
        if not config.provider(provider).enabled:
            return adapter.hook_response(payload), "provider-disabled"
        options = AdapterOptions(include_message_preview=config.privacy.include_message_preview)
        event = adapter.translate(payload, options)
        if event is None:
            return adapter.hook_response(payload), "ignored"
        if capture_host and "activity" not in event.metadata:
            from turnping.platform.windows import find_host_window

            host = find_host_window(event.project_path)
            if host is not None:
                event = event.model_copy(update={"host": host})
        how = deliver(event, paths, config.general.launch_app_on_event)
        return adapter.hook_response(payload), how
    except AdapterError as exc:
        record_failure(
            paths, provider, str(exc), describe_payload_shape(payload) if payload else None
        )
        return adapter.hook_response(payload), "invalid"
    except Exception as exc:
        record_failure(paths, provider, f"{exc.__class__.__name__}: {exc}", None)
        return adapter.hook_response(payload), "error"


def main(provider: str, argv_payload: str | None = None) -> int:
    paths = get_paths()
    with contextlib.suppress(Exception):
        from turnping.runtime import setup_logging

        setup_logging(paths, "INFO", name="hook.log")
    stdin = None if argv_payload else getattr(sys.stdin, "buffer", None)
    response, _how = run_hook(provider, stdin=stdin, argv_payload=argv_payload, paths=paths)
    if response.stdout:
        with contextlib.suppress(Exception):
            sys.stdout.write(response.stdout)
            sys.stdout.flush()
    return 0  # never fail the agent's hook
