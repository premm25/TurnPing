"""How to invoke AgentPing from outside (hooks, URI handler, startup entry).

Frozen (PyInstaller) builds ship two executables side by side:
    AgentPing.exe       windowed desktop app (no console)
    agentping-cli.exe   console CLI, used by agent hooks (needs stdin/stdout)
Development installs use the current interpreter with ``-m agentping``.
"""

from __future__ import annotations

import sys
from pathlib import Path

GUI_EXE = "AgentPing.exe"
CLI_EXE = "agentping-cli.exe"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _interpreter(windowed: bool) -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        name = exe.name.lower()
        if windowed and name == "python.exe":
            candidate = exe.with_name("pythonw.exe")
            if candidate.exists():
                return str(candidate)
        if not windowed and name == "pythonw.exe":
            candidate = exe.with_name("python.exe")
            if candidate.exists():
                return str(candidate)
    return str(exe)


def cli_command() -> list[str]:
    """Command prefix for console invocations (hooks, diagnostics)."""
    if is_frozen():
        return [str(Path(sys.executable).with_name(CLI_EXE))]
    return [_interpreter(windowed=False), "-I", "-m", "agentping"]


def gui_command() -> list[str]:
    """Command prefix that starts the desktop app without a console window."""
    if is_frozen():
        return [str(Path(sys.executable).with_name(GUI_EXE))]
    return [_interpreter(windowed=True), "-I", "-m", "agentping"]


def shell_command(argv: list[str]) -> str:
    """Render a command for agent hook configs that run it through a shell.

    Paths are written with forward slashes and quoted only when they contain spaces:
    an unquoted path is run correctly by bash, cmd.exe *and* PowerShell, while a quoted
    one is treated as a string literal by PowerShell.
    """
    out = []
    for i, part in enumerate(argv):
        text = part.replace("\\", "/") if i == 0 or Path(part).is_absolute() else part
        if any(ch.isspace() for ch in text):
            text = f'"{text}"'
        out.append(text)
    return " ".join(out)
