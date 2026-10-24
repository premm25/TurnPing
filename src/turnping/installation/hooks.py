"""Safe, reversible installation of TurnPing hooks into agent configuration files.

Guarantees (each covered by tests in ``tests/installation``):

1. Locate and parse the existing file; if it is not valid JSON (e.g. contains comments)
   or has an unexpected shape, refuse to touch it and explain why.
2. Back up the original before any change.
3. Preserve every unrelated key, hook group and handler, in order.
4. Identify TurnPing's handlers by their command (``... turnping ... hook <provider>``)
   so installation is idempotent and uninstallation removes only our entries.
5. Write atomically (temp file + rename) and only if the file did not change on disk
   since it was read.
6. Re-read and validate the result; restore the backup if validation fails.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from turnping.installation.backups import backup_file, restore
from turnping.installation.validation import (
    ConfigShapeError,
    command_executable,
    executable_exists,
    validate_hooks_shape,
)
from turnping.launcher import cli_command, shell_command
from turnping.security.paths import get_paths, user_home


class IntegrationStatus(StrEnum):
    OPERATIONAL = "Installed and operational"
    INSTALLED_UNVERIFIED = "Installed but unverified"
    NOT_INSTALLED = "Not installed"
    MISCONFIGURED = "Misconfigured"
    UNSUPPORTED = "Unsupported in the current environment"


@dataclass(frozen=True)
class HookSpec:
    event: str
    matcher: str | None = None
    handler_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class InstallReport:
    provider: str
    ok: bool
    changed: bool
    path: Path | None
    messages: list[str] = field(default_factory=list)
    backup: Path | None = None

    def render(self) -> str:
        head = "OK" if self.ok else "FAILED"
        lines = [f"[{head}] {self.provider}: {self.path or '-'}"]
        lines += [f"  - {m}" for m in self.messages]
        if self.backup:
            lines.append(f"  - backup: {self.backup}")
        return "\n".join(lines)


@dataclass
class StatusReport:
    provider: str
    display_name: str
    status: IntegrationStatus
    detail: str
    path: Path | None
    agent_detected: bool
    notes: list[str] = field(default_factory=list)


class JsonHooksInstaller:
    """Installer for agents whose config holds a ``hooks`` object of matcher groups."""

    provider_id: str = ""
    display_name: str = ""
    binary: str = ""
    notes: tuple[str, ...] = ()

    def config_path(self) -> Path:
        raise NotImplementedError

    def specs(self, options: dict[str, Any] | None = None) -> list[HookSpec]:
        raise NotImplementedError

    # ----- detection ------------------------------------------------------------

    def agent_detected(self) -> bool:
        return bool(shutil.which(self.binary)) or self.config_path().exists()

    def hook_command(self) -> str:
        return shell_command([*cli_command(), "hook", self.provider_id])

    def _marker(self) -> re.Pattern[str]:
        return re.compile(rf"turnping.*\bhook\s+{re.escape(self.provider_id)}\b", re.IGNORECASE)

    def is_ours(self, handler: Any) -> bool:
        return (
            isinstance(handler, dict)
            and isinstance(handler.get("command"), str)
            and bool(self._marker().search(handler["command"]))
        )

    # ----- pure transforms ------------------------------------------------------

    def strip_ours(self, root: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(root)
        hooks = result.get("hooks")
        if not isinstance(hooks, dict):
            return result
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            kept_groups = []
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    kept_groups.append(group)
                    continue
                handlers = [h for h in group["hooks"] if not self.is_ours(h)]
                if len(handlers) == len(group["hooks"]):
                    kept_groups.append(group)
                elif handlers:
                    kept_groups.append({**group, "hooks": handlers})
                # else: the group only contained our handler -> drop it
            if kept_groups:
                hooks[event] = kept_groups
            elif groups:
                del hooks[event]
        # An emptied "hooks" object is deliberately left in place: it is valid for every
        # supported agent, and removing it could drop an empty object the user created.
        return result

    @staticmethod
    def _normalized(root: dict[str, Any]) -> dict[str, Any]:
        """Treat a missing and an empty ``hooks`` object as equivalent for comparisons."""
        if root.get("hooks") == {}:
            return {k: v for k, v in root.items() if k != "hooks"}
        return root

    def add_ours(
        self, root: dict[str, Any], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = self.strip_ours(root)
        hooks = result.setdefault("hooks", {})
        command = self.hook_command()
        for spec in self.specs(options):
            group: dict[str, Any] = {}
            if spec.matcher is not None:
                group["matcher"] = spec.matcher
            group["hooks"] = [{"type": "command", "command": command, **spec.handler_extra}]
            hooks.setdefault(spec.event, []).append(group)
        return result

    # ----- file operations ------------------------------------------------------

    def _read(self, path: Path) -> tuple[dict[str, Any], str | None, float | None]:
        if not path.exists():
            return {}, None, None
        text = path.read_text(encoding="utf-8-sig")
        mtime = path.stat().st_mtime_ns
        if not text.strip():
            return {}, text, mtime
        try:
            root = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigShapeError(
                f"{path} is not plain JSON (line {exc.lineno}: {exc.msg}). It may contain "
                "comments. TurnPing will not rewrite it; see docs/INTEGRATIONS.md for the "
                "manual snippet."
            ) from exc
        validate_hooks_shape(root)
        return root, text, mtime

    def _write(
        self, path: Path, root: dict[str, Any], original_text: str | None, mtime: float | None
    ) -> None:
        if mtime is not None and path.exists() and path.stat().st_mtime_ns != mtime:
            raise RuntimeError(f"{path} changed while TurnPing was editing it; please retry")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(root, indent=2, ensure_ascii=False)
        if original_text is None or original_text.endswith("\n"):
            text += "\n"
        tmp = path.with_name(path.name + ".turnping-tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _apply(self, transform: str, options: dict[str, Any] | None) -> InstallReport:
        path = self.config_path()
        report = InstallReport(self.provider_id, ok=False, changed=False, path=path)
        try:
            root, original_text, mtime = self._read(path)
        except (ConfigShapeError, OSError, UnicodeDecodeError) as exc:
            report.messages.append(str(exc))
            return report
        new_root = self.add_ours(root, options) if transform == "install" else self.strip_ours(root)
        if self._normalized(new_root) == self._normalized(root):
            report.ok = True
            report.messages.append(
                "already installed; nothing to change"
                if transform == "install"
                else "no TurnPing hooks found; nothing to remove"
            )
            return report
        if original_text is not None:
            report.backup = backup_file(path, get_paths().backup_dir, self.provider_id)
        try:
            self._write(path, new_root, original_text, mtime)
            reread, _, _ = self._read(path)
            if self._normalized(self.strip_ours(reread)) != self._normalized(self.strip_ours(root)):
                raise RuntimeError("unrelated settings changed unexpectedly")
            if transform == "install" and self.installed_events(reread) != {
                s.event for s in self.specs(options)
            }:
                raise RuntimeError("TurnPing hooks missing after write")
        except Exception as exc:
            if report.backup is not None:
                restore(report.backup, path)
                report.messages.append(f"write failed ({exc}); original restored from backup")
            elif original_text is None and path.exists():
                path.unlink()
                report.messages.append(f"write failed ({exc}); removed the new file")
            else:
                report.messages.append(f"write failed: {exc}")
            return report
        report.ok = True
        report.changed = True
        if transform == "install":
            report.messages.append(
                f"added hooks: {', '.join(s.event for s in self.specs(options))}"
            )
            report.messages.append(f"hook command: {self.hook_command()}")
            report.messages.extend(self.notes)
        else:
            report.messages.append("removed TurnPing hooks; other settings left untouched")
        return report

    def install(self, options: dict[str, Any] | None = None) -> InstallReport:
        return self._apply("install", options)

    def uninstall(self) -> InstallReport:
        return self._apply("uninstall", None)

    # ----- status ---------------------------------------------------------------

    def installed_events(self, root: dict[str, Any]) -> set[str]:
        events: set[str] = set()
        hooks = root.get("hooks")
        if not isinstance(hooks, dict):
            return events
        for event, groups in hooks.items():
            for group in groups if isinstance(groups, list) else []:
                for handler in group.get("hooks", []) if isinstance(group, dict) else []:
                    if self.is_ours(handler):
                        events.add(event)
        return events

    def our_commands(self, root: dict[str, Any]) -> set[str]:
        out: set[str] = set()
        for groups in (root.get("hooks") or {}).values():
            for group in groups if isinstance(groups, list) else []:
                for handler in group.get("hooks", []) if isinstance(group, dict) else []:
                    if self.is_ours(handler):
                        out.add(handler["command"])
        return out

    def status(
        self, last_event_seen: Any = None, options: dict[str, Any] | None = None
    ) -> StatusReport:
        path = self.config_path()
        detected = self.agent_detected()

        def report(status: IntegrationStatus, detail: str) -> StatusReport:
            return StatusReport(
                self.provider_id,
                self.display_name,
                status,
                detail,
                path,
                detected,
                list(self.notes),
            )

        try:
            root, _, _ = self._read(path)
        except (ConfigShapeError, OSError, UnicodeDecodeError) as exc:
            return report(IntegrationStatus.MISCONFIGURED, str(exc))
        events = self.installed_events(root)
        if not events:
            if not detected:
                return report(
                    IntegrationStatus.NOT_INSTALLED,
                    f"{self.display_name} was not found on this machine",
                )
            return report(
                IntegrationStatus.NOT_INSTALLED, f"run: turnping install {self.provider_id}"
            )
        expected = {s.event for s in self.specs(options)}
        missing = expected - events
        broken = [c for c in self.our_commands(root) if not executable_exists(c)]
        if broken:
            exe = command_executable(broken[0])
            return report(
                IntegrationStatus.MISCONFIGURED,
                f"hook points to a missing program ({exe}); re-run: turnping install {self.provider_id}",
            )
        current = self.hook_command()
        if any(c != current for c in self.our_commands(root)):
            return report(
                IntegrationStatus.MISCONFIGURED,
                f"hooks point to a different TurnPing install; re-run: turnping install {self.provider_id}",
            )
        if missing:
            return report(
                IntegrationStatus.MISCONFIGURED,
                f"some hooks are missing ({', '.join(sorted(missing))}); re-run install",
            )
        if last_event_seen is not None:
            return report(
                IntegrationStatus.OPERATIONAL,
                f"hooks installed; last event {last_event_seen.astimezone():%Y-%m-%d %H:%M}",
            )
        return report(
            IntegrationStatus.INSTALLED_UNVERIFIED,
            "hooks installed; no event received yet (use the agent once to verify)",
        )


# ----- providers ------------------------------------------------------------------

CLAUDE_NOTIFICATION_TYPES = [
    "permission_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
    "quota_auto_resume_stale",
    "quota_auto_resume_disabled",
    "quota_auto_resume_fired",
    "elicitation_response",
]


class ClaudeCodeInstaller(JsonHooksInstaller):
    provider_id = "claude-code"
    display_name = "Claude Code"
    binary = "claude"

    def config_path(self) -> Path:
        return user_home() / ".claude" / "settings.json"

    def specs(self, options: dict[str, Any] | None = None) -> list[HookSpec]:
        options = options or {}
        types = list(CLAUDE_NOTIFICATION_TYPES)
        if options.get("notify_idle_prompt"):
            types.append("idle_prompt")
        sync = {"timeout": 10}
        bg = {"async": True}
        return [
            HookSpec("Stop", None, sync),
            HookSpec("StopFailure", None, sync),
            HookSpec("Notification", "|".join(types), sync),
            HookSpec("SessionStart", None, bg),
            HookSpec("SessionEnd", None, {"timeout": 5}),
            HookSpec("UserPromptSubmit", None, bg),
            HookSpec("PostToolUse", "*", bg),
        ]


class CodexInstaller(JsonHooksInstaller):
    provider_id = "codex"
    display_name = "Codex CLI"
    binary = "codex"
    notes = ("Codex requires you to review and trust new hooks: open Codex and run /hooks.",)

    def config_path(self) -> Path:
        return user_home() / ".codex" / "hooks.json"

    def agent_detected(self) -> bool:
        return bool(shutil.which(self.binary)) or (user_home() / ".codex").is_dir()

    def specs(self, options: dict[str, Any] | None = None) -> list[HookSpec]:
        bg = {"async": True, "timeout": 30}
        return [
            # Stop must answer with JSON on stdout, so it runs synchronously (prints {}).
            HookSpec("Stop", None, {"timeout": 10}),
            # Background hooks cannot approve or deny: a hard guarantee that TurnPing
            # never decides a permission request.
            HookSpec("PermissionRequest", None, bg),
            HookSpec("SessionStart", None, bg),
            HookSpec("SessionEnd", None, {"timeout": 3}),
            HookSpec("UserPromptSubmit", None, bg),
            HookSpec("PostToolUse", None, bg),
        ]


class GeminiCliInstaller(JsonHooksInstaller):
    provider_id = "gemini-cli"
    display_name = "Gemini CLI"
    binary = "gemini"
    notes = ("Reminder escalation is disabled for Gemini CLI (see docs/INTEGRATIONS.md).",)

    def config_path(self) -> Path:
        return user_home() / ".gemini" / "settings.json"

    def agent_detected(self) -> bool:
        return bool(shutil.which(self.binary)) or self.config_path().exists()

    def specs(self, options: dict[str, Any] | None = None) -> list[HookSpec]:
        def h(name: str) -> dict[str, Any]:
            return {"name": f"turnping-{name}", "timeout": 10000}  # milliseconds

        return [
            HookSpec("AfterAgent", None, h("after-agent")),
            HookSpec("Notification", None, h("notification")),
            HookSpec("SessionStart", None, h("session-start")),
            HookSpec("SessionEnd", None, h("session-end")),
            HookSpec("BeforeAgent", None, h("before-agent")),
        ]


INSTALLERS: dict[str, JsonHooksInstaller] = {
    i.provider_id: i for i in (ClaudeCodeInstaller(), CodexInstaller(), GeminiCliInstaller())
}


def get_installer(provider_id: str) -> JsonHooksInstaller | None:
    return INSTALLERS.get(provider_id)
