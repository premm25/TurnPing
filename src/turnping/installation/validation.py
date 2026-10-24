"""Structural validation of ``{"hooks": {Event: [{matcher?, hooks: [handler]}]}}`` configs.

Claude Code, Codex (hooks.json) and Gemini CLI share this shape.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any


class ConfigShapeError(ValueError):
    pass


def validate_hooks_shape(root: Any) -> None:
    if not isinstance(root, dict):
        raise ConfigShapeError("top level must be a JSON object")
    hooks = root.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise ConfigShapeError("'hooks' must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ConfigShapeError(f"hooks.{event} must be a list")
        for i, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ConfigShapeError(f"hooks.{event}[{i}] must be an object")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise ConfigShapeError(f"hooks.{event}[{i}].hooks must be a list")
            for j, handler in enumerate(handlers):
                if not isinstance(handler, dict) or "type" not in handler:
                    raise ConfigShapeError(f"hooks.{event}[{i}].hooks[{j}] must have a 'type'")


def command_executable(command: str) -> str | None:
    """First token of a hook command (handles quoted Windows paths)."""
    command = command.strip()
    if not command:
        return None
    if command[0] in "\"'":
        end = command.find(command[0], 1)
        return command[1:end] if end > 0 else None
    match = re.match(r"\S+", command)
    if not match:
        return None
    try:
        return shlex.split(match.group(0), posix=False)[0]
    except ValueError:
        return match.group(0)


def executable_exists(command: str) -> bool:
    exe = command_executable(command)
    return bool(exe) and Path(exe).exists()  # type: ignore[arg-type]
