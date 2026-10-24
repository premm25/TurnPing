"""Windows-specific helpers: host-window discovery, focusing, registry integration.

Host-window discovery (run inside the hook process):
    The hook is a descendant of the agent process, which is a descendant of the shell,
    which is hosted by a terminal or editor (Windows Terminal, VS Code, conhost...). We
    walk the parent-process chain and pick the nearest ancestor that owns a visible,
    unowned top-level window. That is far more reliable than guessing by window title.

Limitations (documented in docs/TROUBLESHOOTING.md):
    * We can focus the terminal/editor *window*, not a specific tab or split pane.
    * If the window was closed and its handle reused, we detect the mismatch (pid/exe
      check) and fall back to the details view instead of focusing a stranger's window.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass

from turnping.contracts.events import HostWindow

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

_TERMINAL_HINTS = {
    "windowsterminal.exe": "windows-terminal",
    "code.exe": "vscode",
    "code - insiders.exe": "vscode-insiders",
    "cursor.exe": "cursor",
    "windsurf.exe": "windsurf",
    "conhost.exe": "console",
    "openconsole.exe": "console",
    "wezterm-gui.exe": "wezterm",
    "alacritty.exe": "alacritty",
    "idea64.exe": "jetbrains",
    "pycharm64.exe": "jetbrains",
    "webstorm64.exe": "jetbrains",
    "mintty.exe": "mintty",
    "tabby.exe": "tabby",
    "hyper.exe": "hyper",
}

# Ancestors we never treat as the "host" even if they own windows.
_SKIP = {"explorer.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe"}


@dataclass(frozen=True)
class _Proc:
    pid: int
    ppid: int
    exe: str


def _process_table() -> dict[int, _Proc]:
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    table: dict[int, _Proc] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = _Proc(
                entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()
            )
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return table


def _top_level_windows() -> dict[int, list[tuple[int, str]]]:
    """pid -> [(hwnd, title)] for visible, unowned, titled top-level windows."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    result: dict[int, list[tuple[int, str]]] = {}
    GW_OWNER = 4
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        # InternalGetWindowText never sends messages, so a hung app cannot stall us.
        buf = ctypes.create_unicode_buffer(256)
        if not user32.InternalGetWindowText(hwnd, buf, 256):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.setdefault(pid.value, []).append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return result


def _pick_window(candidates: list[tuple[int, str]], project_path: str | None) -> tuple[int, str]:
    """One editor process (e.g. VS Code) can own many windows; prefer the project's."""
    if project_path and len(candidates) > 1:
        name = project_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
        if name:
            for hwnd, title in candidates:
                parts = [p.strip().lower() for p in title.split(" - ")]
                if name in parts:
                    return hwnd, title
            for hwnd, title in candidates:
                if name in title.lower():
                    return hwnd, title
    return candidates[0]


def find_host_window(project_path: str | None = None, max_depth: int = 32) -> HostWindow | None:
    """Locate the terminal/editor window hosting the current process tree."""
    if not IS_WINDOWS:
        return None
    try:
        table = _process_table()
        windows = _top_level_windows()
        pid = os.getpid()
        seen: set[int] = set()
        terminal_hint = None
        if os.environ.get("TERM_PROGRAM") == "vscode":
            terminal_hint = "vscode"
        elif os.environ.get("WT_SESSION"):
            terminal_hint = "windows-terminal"
        for _ in range(max_depth):
            proc = table.get(pid)
            if proc is None or pid in seen:
                break
            seen.add(pid)
            if proc.exe not in _SKIP and pid in windows:
                hwnd, title = _pick_window(windows[pid], project_path)
                return HostWindow(
                    hwnd=hwnd,
                    pid=pid,
                    process_name=proc.exe,
                    title=title,
                    terminal=_TERMINAL_HINTS.get(proc.exe, terminal_hint),
                )
            pid = proc.ppid
        if terminal_hint:
            return HostWindow(terminal=terminal_hint)
    except Exception as exc:  # discovery is best effort and must never break a hook
        log.debug("host window discovery failed: %s", exc)
    return None


def _window_pid(hwnd: int) -> int | None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not user32.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def focus_window(host: HostWindow) -> tuple[bool, str]:
    """Bring the host window to the foreground after verifying it is still the same app."""
    if not IS_WINDOWS:
        return False, "window focusing is only implemented on Windows"
    if not host.hwnd:
        return False, "the integration did not report a window for this session"
    owner = _window_pid(host.hwnd)
    if owner is None:
        return False, "the session's window no longer exists"
    if host.pid and owner != host.pid:
        return False, "the session's window was closed (its handle now belongs to another app)"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    SW_RESTORE = 9
    if user32.IsIconic(host.hwnd):
        user32.ShowWindow(host.hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(host.hwnd):
        return True, f"focused {host.process_name or 'window'}"
    # Windows restricts foreground changes; a synthetic Alt press is the documented-in-
    # practice way for the process the user just interacted with to hand over focus.
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    if user32.SetForegroundWindow(host.hwnd):
        return True, f"focused {host.process_name or 'window'}"
    user32.FlashWindow(host.hwnd, True)
    return False, "Windows did not allow focusing the window; its taskbar button is flashing"


def allow_any_foreground() -> None:
    """Let another process (the running TurnPing app) take the foreground."""
    if IS_WINDOWS:
        ASFW_ANY = 0xFFFFFFFF
        ctypes.WinDLL("user32").AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))


def open_in_vscode(path: str) -> tuple[bool, str]:
    """Focus the VS Code window that has ``path`` open (``code <folder>`` reuses it)."""
    import shutil

    exe = shutil.which("code")
    if not exe or not os.path.isdir(path):
        return False, "VS Code CLI not found"
    try:
        # Argument list (no shell); the path is passed verbatim, never interpreted.
        subprocess.Popen([exe, path], close_fds=True, creationflags=_no_window_flags())
        return True, "asked VS Code to focus the project window"
    except OSError as exc:
        return False, f"could not start VS Code: {exc}"


def open_folder(path: str) -> tuple[bool, str]:
    if not os.path.isdir(path):
        return False, "folder does not exist"
    try:
        os.startfile(path)  # noqa: S606 - opens Explorer on a folder
        return True, "opened folder"
    except OSError as exc:
        return False, str(exc)


def _no_window_flags() -> int:
    return 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


# ----- registry integration (per-user, HKCU only) ---------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TurnPing"


def _winreg():
    import winreg

    return winreg


def register_uri_scheme(scheme: str, command: list[str]) -> None:
    """Register ``scheme://`` so toast clicks can reach TurnPing (HKCU, no admin)."""
    if not IS_WINDOWS:
        return
    winreg = _winreg()
    base = rf"Software\Classes\{scheme}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:TurnPing")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, subprocess.list2cmdline(command) + ' "%1"')


def uri_scheme_command(scheme: str) -> str | None:
    if not IS_WINDOWS:
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}\shell\open\command"
        ) as key:
            return str(winreg.QueryValueEx(key, "")[0])
    except FileNotFoundError:
        return None


def unregister_uri_scheme(scheme: str) -> bool:
    if not IS_WINDOWS:
        return False
    winreg = _winreg()
    removed = False
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}{sub}")
            removed = True
        except FileNotFoundError:
            continue
    return removed


def set_startup(command: list[str] | None) -> None:
    """Enable (command) or disable (None) launching TurnPing at Windows sign-in."""
    if not IS_WINDOWS:
        return
    winreg = _winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(command))


def get_startup() -> str | None:
    if not IS_WINDOWS:
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, RUN_VALUE)[0])
    except FileNotFoundError:
        return None


def windows_version() -> str:
    if not IS_WINDOWS:
        return sys.platform
    v = sys.getwindowsversion()
    name = "Windows 11" if v.build >= 22000 else "Windows 10" if v.major == 10 else "Windows"
    return f"{name} (build {v.build})"
