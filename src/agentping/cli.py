"""AgentPing command-line interface."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from typing import Any

from agentping import __version__
from agentping.contracts.events import EventCategory

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def _print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


# ----- commands ---------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    from agentping.desktop.application import run_app

    return run_app(background=args.background, show_event=args.show_event)


def cmd_doctor(args: argparse.Namespace) -> int:
    from agentping.core.health import Level, format_checks, run_checks
    from agentping.security.paths import get_paths

    repo = _readonly_repo()
    checks = run_checks(get_paths(), repo)
    if args.json:
        _print(json.dumps([c.__dict__ | {"level": c.level.value} for c in checks], indent=2))
    else:
        _print(f"AgentPing {__version__} diagnostics\n")
        _print(format_checks(checks))
    return EXIT_FAIL if any(c.level is Level.ERROR for c in checks) else EXIT_OK


def cmd_test_notification(args: argparse.Namespace) -> int:
    from agentping.core.health import make_test_event
    from agentping.ingest.ipc import send_message

    event = make_test_event(args.category)
    try:
        response = send_message({"type": "test_notification", "event": event.to_wire()})
        via = "the running AgentPing app"
    except ConnectionError:
        response = _process_locally(event)
        via = "this process (the desktop app is not running, so popups are unavailable)"
    _print(f"Sent a test {args.category} event through the notification pipeline via {via}.")
    status = response.get("delivery_status")
    _print(f"  decision: {response.get('decision')} ({response.get('reason')})")
    _print(f"  delivery: {status or 'not attempted'}")
    if response.get("delivery_detail"):
        _print(f"  detail:   {response['delivery_detail']}")
    if status == "confirmed":
        _print("Windows confirmed the notification is in the Action Center.")
    elif status == "dispatched":
        _print("Handed to the display backend; Windows did not confirm it was shown.")
    else:
        _print("No notification was displayed. Run `agentping doctor` for details.")
    return EXIT_OK if status in ("confirmed", "dispatched") else EXIT_FAIL


def _process_locally(event: Any) -> dict[str, Any]:
    from agentping.runtime import build_runtime

    rt = build_runtime()
    try:
        result = rt.router.process(event)
    finally:
        rt.close()
    return _result_dict(result)


def _result_dict(result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "decision": result.decision.action.value,
        "reason": result.decision.reason,
        "delivery_status": result.delivery.status.value if result.delivery else None,
        "delivery_detail": result.delivery.detail if result.delivery else None,
    }


def cmd_integrations(args: argparse.Namespace) -> int:
    from agentping.core.health import integration_statuses

    reports = integration_statuses(_readonly_repo())
    if args.json:
        _print(
            json.dumps(
                [
                    {
                        "provider": r.provider,
                        "status": r.status.value,
                        "detail": r.detail,
                        "config": str(r.path) if r.path else None,
                        "agent_detected": r.agent_detected,
                    }
                    for r in reports
                ],
                indent=2,
            )
        )
        return EXIT_OK
    for r in reports:
        _print(f"{r.display_name:<12} {r.status.value}")
        _print(f"{'':<12} {r.detail}")
        _print(f"{'':<12} config: {r.path}")
    _print(
        "\nGeneric integration: always available via `agentping send` (see docs/INTEGRATIONS.md)."
    )
    return EXIT_OK


def cmd_install(args: argparse.Namespace) -> int:
    from agentping.config import load_config
    from agentping.installation.hooks import INSTALLERS
    from agentping.installation.system import register_system_integration, set_start_with_windows

    if args.no_hooks:
        providers: list[str] = []
    else:
        providers = args.providers or [p for p, i in INSTALLERS.items() if i.agent_detected()]
    unknown = [p for p in providers if p not in INSTALLERS]
    if unknown:
        _print(f"unknown provider(s): {', '.join(unknown)}. Known: {', '.join(INSTALLERS)}")
        return EXIT_USAGE
    if not providers and not args.no_hooks:
        _print("No supported coding agent was detected. Install one, or name it explicitly:")
        _print(f"  agentping install {' | '.join(INSTALLERS)}")
    config = load_config().config
    ok = True
    for pid in providers:
        options = {"notify_idle_prompt": config.provider(pid).notify_idle_prompt}
        report = INSTALLERS[pid].install(options)
        ok &= report.ok
        _print(report.render())
    try:
        register_system_integration()
        _print("[OK] registered toast app id and agentping:// click handler (per-user)")
        if args.startup:
            set_start_with_windows(True)
            _print("[OK] AgentPing will start when you sign in to Windows")
    except OSError as exc:
        ok = False
        _print(f"[FAILED] system registration: {exc}")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_uninstall(args: argparse.Namespace) -> int:
    from agentping.installation.hooks import INSTALLERS
    from agentping.installation.system import unregister_system_integration
    from agentping.security.paths import get_paths

    providers = args.providers or list(INSTALLERS)
    ok = True
    for pid in providers:
        installer = INSTALLERS.get(pid)
        if installer is None:
            _print(f"unknown provider: {pid}")
            ok = False
            continue
        report = installer.uninstall()
        ok &= report.ok
        _print(report.render())
    if not args.providers:
        removed = unregister_system_integration()
        _print(f"[OK] removed system registration: {', '.join(removed) or 'nothing registered'}")
        paths = get_paths()
        if args.purge_data:
            from agentping.ingest.ipc import is_app_running

            if is_app_running():
                _print(
                    "[SKIPPED] AgentPing is running; run `agentping quit`, then re-run with --purge-data"
                )
                ok = False
            else:
                backups = paths.backup_dir
                kept = None
                if backups.exists() and any(backups.rglob("*.bak")):
                    kept = paths.home.parent / "AgentPing-settings-backups"
                    shutil.copytree(backups, kept, dirs_exist_ok=True)
                from agentping.runtime import close_logging

                close_logging()  # our own log file would otherwise keep the folder locked
                failed: list[str] = []
                shutil.rmtree(paths.home, onexc=lambda _f, path, _e: failed.append(path))
                if failed or paths.home.exists():
                    ok = False
                    _print(f"[FAILED] could not delete {len(failed)} item(s) under {paths.home}")
                    for item in failed[:5]:
                        _print(f"     {item}")
                    _print("     close AgentPing (and any tool holding these files) and retry")
                else:
                    _print(f"[OK] deleted AgentPing data: {paths.home}")
                if kept:
                    _print(f"     agent settings backups were preserved in {kept}")
        else:
            _print(f"Kept history and preferences in {paths.home}")
            _print("  (run `agentping uninstall --purge-data` to delete them)")
        _print(
            "AgentPing never touches your projects. Remove the program files/venv yourself if desired."
        )
    return EXIT_OK if ok else EXIT_FAIL


def cmd_history(args: argparse.Namespace) -> int:
    repo = _readonly_repo()
    if repo is None:
        _print("No history yet.")
        return EXIT_OK
    records = repo.list_events(
        limit=args.limit, provider=args.provider, category=args.category, search=args.search
    )
    if args.json:
        _print(
            json.dumps(
                [
                    {
                        "event_id": r.event_id,
                        "received_at": r.received_at.isoformat(),
                        "provider": r.provider,
                        "category": r.category,
                        "project": r.project_path,
                        "session_id": r.session_id,
                        "decision": r.decision,
                        "reason": r.decision_reason,
                        "delivery": r.delivery_status,
                        "message": r.message,
                        "reminder": r.is_reminder,
                        "dismissed": r.dismissed,
                    }
                    for r in records
                ],
                indent=2,
            )
        )
        return EXIT_OK
    if not records:
        _print("No matching events.")
    for r in records:
        when = r.received_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        project = (r.project_path or "-").replace("\\", "/").rsplit("/", 1)[-1]
        tag = " [reminder]" if r.is_reminder else ""
        _print(
            f"{when}  {r.provider:<12} {r.category:<18} {project:<20} {r.decision}/{r.delivery_status}{tag}"
        )
        if r.message:
            _print(f"{'':<21}{r.message}")
    return EXIT_OK


def cmd_hook(args: argparse.Namespace) -> int:
    from agentping.hook import main as hook_main

    return hook_main(args.provider, args.payload)


def cmd_send(args: argparse.Namespace) -> int:
    from agentping.adapters.base import AdapterError
    from agentping.adapters.registry import default_registry
    from agentping.config import load_config
    from agentping.hook import deliver, read_stdin
    from agentping.security.paths import get_paths

    if args.json:
        raw = read_stdin(sys.stdin.buffer)
        if len(raw) > 64 * 1024:
            _print("error: payload exceeds 64 KiB")
            return EXIT_USAGE
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _print(f"error: invalid JSON on stdin: {exc}")
            return EXIT_USAGE
    else:
        if not args.provider or not args.category:
            _print("error: --provider and --category are required (or use --json)")
            return EXIT_USAGE
        payload = {"provider": args.provider, "category": args.category}
        for key in ("message", "title", "session_id", "project_path", "event_id"):
            value = getattr(args, key)
            if value is not None:
                payload[key] = value
    adapter = default_registry().get("generic")
    assert adapter is not None
    try:
        event = adapter.translate(payload)
    except AdapterError as exc:
        _print(f"error: {exc}")
        return EXIT_USAGE
    assert event is not None
    how = deliver(event, get_paths(), load_config().config.general.launch_app_on_event)
    _print(f"accepted event {event.event_id} ({how})")
    return EXIT_OK


def cmd_open_uri(args: argparse.Namespace) -> int:
    from agentping.core.navigation import navigate_to_event, parse_open_uri
    from agentping.ingest.ipc import send_message
    from agentping.platform.windows import allow_any_foreground

    event_id = parse_open_uri(args.uri)
    if event_id is None:
        return EXIT_USAGE  # malformed or foreign URI: do nothing
    repo = _readonly_repo()
    result = None
    if repo is not None:
        result, _ = navigate_to_event(repo, event_id)
    try:
        if result is not None and result.ok:
            send_message({"type": "command", "command": "acknowledge", "event_id": event_id})
        else:
            allow_any_foreground()
            send_message({"type": "command", "command": "open_event", "event_id": event_id})
    except ConnectionError:
        if result is None or not result.ok:
            from agentping.hook import _launch_app_with_args

            _launch_app_with_args(["run", "--show-event", event_id])
    return EXIT_OK


def cmd_pause(args: argparse.Namespace) -> int:
    return _set_paused(True)


def cmd_resume(args: argparse.Namespace) -> int:
    return _set_paused(False)


def cmd_quit(args: argparse.Namespace) -> int:
    from agentping.ingest.ipc import send_message

    try:
        response = send_message({"type": "command", "command": "quit"})
    except ConnectionError:
        _print("AgentPing is not running.")
        return EXIT_OK
    if not response.get("ok"):
        _print(f"AgentPing refused to exit: {response.get('error')} (exit it from the tray menu)")
        return EXIT_FAIL
    _print("Asked AgentPing to exit.")
    return EXIT_OK


def _set_paused(paused: bool) -> int:
    from agentping.ingest.ipc import send_message

    try:
        send_message({"type": "command", "command": "pause" if paused else "resume"})
    except ConnectionError:
        from agentping.runtime import build_runtime

        rt = build_runtime(toast=False)
        try:
            rt.router.set_paused(paused)
        finally:
            rt.close()
    _print("Notifications paused." if paused else "Notifications resumed.")
    return EXIT_OK


def _readonly_repo():
    from agentping.security.paths import get_paths
    from agentping.storage.database import Database
    from agentping.storage.repository import Repository

    paths = get_paths()
    if not paths.database.exists():
        return None
    return Repository(Database(paths.database))


# ----- parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentping",
        description="AgentPing: one ping, every agent. Desktop notifications for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"AgentPing {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("run", help="start the desktop app (tray, dashboard, reminders)")
    p.add_argument("--background", action="store_true", help="start minimized to the tray")
    p.add_argument("--show-event", metavar="EVENT_ID", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("doctor", help="check configuration, notifications and integrations")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    categories = [c.value for c in EventCategory if c is not EventCategory.UNKNOWN]
    p = sub.add_parser(
        "test-notification", help="send a labelled test event through the real pipeline"
    )
    p.add_argument("--category", default="TASK_COMPLETED", choices=categories)
    p.set_defaults(func=cmd_test_notification)

    p = sub.add_parser("integrations", help="show the status of each coding-agent integration")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_integrations)

    p = sub.add_parser("install", help="install hooks for detected (or named) agents")
    p.add_argument(
        "providers", nargs="*", metavar="provider", help="claude-code, codex, gemini-cli"
    )
    p.add_argument("--startup", action="store_true", help="also start AgentPing at Windows sign-in")
    p.add_argument("--no-hooks", action="store_true", help="only register AgentPing with Windows")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="remove AgentPing hooks and registrations")
    p.add_argument("providers", nargs="*", metavar="provider")
    p.add_argument("--purge-data", action="store_true", help="also delete history and preferences")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("history", help="show recent notification history")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--provider")
    p.add_argument("--category", choices=[c.value for c in EventCategory])
    p.add_argument("--search")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("send", help="send a custom event (generic integration)")
    p.add_argument("--json", action="store_true", help="read a generic event object from stdin")
    p.add_argument("--provider")
    p.add_argument("--category", choices=categories)
    p.add_argument("--message")
    p.add_argument("--title")
    p.add_argument("--session", dest="session_id")
    p.add_argument("--project", dest="project_path")
    p.add_argument("--event-id", dest="event_id")
    p.set_defaults(func=cmd_send)

    sub.add_parser("pause", help="pause notifications").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="resume notifications").set_defaults(func=cmd_resume)
    sub.add_parser("quit", help="exit the running desktop app").set_defaults(func=cmd_quit)

    p = sub.add_parser("hook", help="(used by agent hooks) handle one native hook event")
    p.add_argument("provider")
    p.add_argument("payload", nargs="?", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("open-uri", help=argparse.SUPPRESS)
    p.add_argument("uri")
    p.set_defaults(func=cmd_open_uri)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_OK
    if args.command not in ("hook", "run"):
        from agentping.runtime import setup_logging
        from agentping.security.paths import get_paths

        try:
            setup_logging(get_paths().ensure(), "INFO", name="cli.log")
        except OSError:
            pass
    return int(args.func(args) or 0)


def gui_main() -> int:
    """Entry point of the windowed executable: no arguments means ``run``."""
    return main(sys.argv[1:] or ["run"])
