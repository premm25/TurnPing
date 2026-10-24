"""Per-user filesystem locations.

Everything AgentPing persists lives under one per-user directory. On Windows this is
``%LOCALAPPDATA%\\AgentPing``. Set ``AGENTPING_HOME`` to relocate it (used by tests and
portable setups). AgentPing never writes into project directories or its install dir.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _windows_local_appdata() -> str | None:
    """LocalAppData via the shell API: agents may run hooks with a sanitized env."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("d1", wintypes.DWORD),
            ("d2", wintypes.WORD),
            ("d3", wintypes.WORD),
            ("d4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_LocalAppData {F1B32785-6FBA-4FCF-9D55-7B8E7F157091}
    folder = GUID(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    out = ctypes.c_wchar_p()
    try:
        if (
            ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder), 0, None, ctypes.byref(out)
            )
            != 0
        ):
            return None
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def current_user() -> str:
    """The OS account name, independent of environment variables."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        size = wintypes.DWORD(257)
        buf = ctypes.create_unicode_buffer(257)
        if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)):
            return buf.value
    import getpass

    try:
        return getpass.getuser()
    except Exception:
        return str(os.getuid()) if hasattr(os, "getuid") else "user"


def _default_home() -> Path:
    override = os.environ.get("AGENTPING_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or _windows_local_appdata()
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "AgentPing"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "agentping"


@dataclass(frozen=True)
class AppPaths:
    home: Path

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def database(self) -> Path:
        return self.home / "agentping.db"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def spool_dir(self) -> Path:
        return self.home / "spool"

    @property
    def backup_dir(self) -> Path:
        return self.home / "backups"

    @property
    def ipc_key_file(self) -> Path:
        return self.home / "ipc.key"

    @property
    def hook_failures_file(self) -> Path:
        return self.home / "logs" / "hook-failures.jsonl"

    @property
    def launch_lock_file(self) -> Path:
        return self.home / "launch.lock"

    def ensure(self) -> AppPaths:
        for d in (self.home, self.log_dir, self.spool_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def get_paths() -> AppPaths:
    """Resolve paths at call time so ``AGENTPING_HOME`` changes are honoured."""
    return AppPaths(_default_home())


def user_home() -> Path:
    """Home directory used to locate agent configuration (overridable for tests)."""
    override = os.environ.get("AGENTPING_USER_HOME")
    return Path(override) if override else Path.home()
