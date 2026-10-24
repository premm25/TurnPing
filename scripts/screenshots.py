"""Render README screenshots of the real UI, fed by labelled demo events.

The events go through the real pipeline (adapters -> router -> history) in a throwaway
data directory; only the OS toast is replaced so nothing pops up while rendering.

    python scripts/screenshots.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "screenshots"


def main() -> int:
    os.environ["AGENTPING_HOME"] = tempfile.mkdtemp(prefix="agentping-shots-")
    os.environ["AGENTPING_USER_HOME"] = tempfile.mkdtemp(prefix="agentping-shots-user-")

    from PySide6.QtWidgets import QApplication

    from agentping.adapters.claude_code import ClaudeCodeAdapter
    from agentping.adapters.codex import CodexAdapter
    from agentping.contracts.events import EventCategory, HostWindow, Priority
    from agentping.core.engine import Engine
    from agentping.desktop import theme
    from agentping.desktop.controller import Controller
    from agentping.desktop.main_window import MainWindow
    from agentping.desktop.popup import PopupWidget
    from agentping.notifications.base import (
        BackendStatus,
        DeliveryResult,
        DeliveryStatus,
        Notification,
        NotificationBackend,
    )
    from agentping.runtime import build_runtime

    class DemoToast(NotificationBackend):
        name = "windows-toast"

        def status(self) -> BackendStatus:
            return BackendStatus(True, "Windows notifications are enabled")

        def show(self, notification: Notification) -> DeliveryResult:
            return DeliveryResult(DeliveryStatus.CONFIRMED, self.name, "in Action Center")

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    dark = "--dark" in sys.argv
    app.setStyleSheet(theme.stylesheet(theme.DARK if dark else theme.LIGHT))
    if dark:
        theme.current = lambda: theme.DARK  # status colors follow the rendered palette
    rt = build_runtime(toast=False)
    rt.notifier.add_backend("toast", DemoToast())
    claude, codex = ClaudeCodeAdapter(), CodexAdapter()
    code = HostWindow(hwnd=1, pid=1, process_name="code.exe", terminal="vscode")
    wt = HostWindow(hwnd=2, pid=2, process_name="windowsterminal.exe", terminal="windows-terminal")
    demo = [
        (
            claude,
            {
                "hook_event_name": "SessionStart",
                "session_id": "demo-a1",
                "cwd": "C:/src/storefront",
                "source": "startup",
            },
            code,
        ),
        (
            claude,
            {
                "hook_event_name": "Stop",
                "session_id": "demo-a1",
                "prompt_id": "1",
                "cwd": "C:/src/storefront",
            },
            code,
        ),
        (
            codex,
            {
                "hook_event_name": "Stop",
                "session_id": "demo-c7",
                "turn_id": "t1",
                "cwd": "C:/src/payments-api",
            },
            wt,
        ),
        (
            claude,
            {
                "hook_event_name": "Notification",
                "session_id": "demo-b2",
                "prompt_id": "2",
                "cwd": "C:/src/storefront",
                "notification_type": "permission_prompt",
                "message": "Claude needs your permission to use Bash",
            },
            wt,
        ),
        (
            claude,
            {
                "hook_event_name": "StopFailure",
                "session_id": "demo-d4",
                "prompt_id": "3",
                "cwd": "C:/src/ml-pipeline",
                "error": "rate_limit",
            },
            code,
        ),
    ]
    for adapter, payload, host in demo:
        event = adapter.translate(payload).model_copy(update={"host": host})
        rt.router.process(event)
        time.sleep(0.01)
    engine = Engine(rt.router)
    engine.start()
    controller = Controller(rt, engine)
    window = MainWindow(controller)
    window.resize(1180, 820)
    OUT.mkdir(parents=True, exist_ok=True)

    def settle(ms: int = 1200) -> None:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    window.show_page("dashboard")
    settle()
    window.grab().save(str(OUT / ("dashboard-dark.png" if dark else "dashboard.png")))
    if dark:
        window.hide()
        engine.stop()
        rt.close()
        return 0
    window.show_page("history")
    settle()
    window.history.table.selectRow(1)
    settle(300)
    window.grab().save(str(OUT / "history.png"))
    window.show_page("settings")
    settle(400)
    window.grab().save(str(OUT / "settings.png"))
    popup = PopupWidget(
        Notification(
            event_id="demo0001",
            title="Claude Code needs your permission",
            body="Claude needs your permission to use Bash",
            attribution="storefront \u00b7 session demo-b2",
            priority=Priority.URGENT,
            category=EventCategory.PERMISSION_NEEDED,
        )
    )
    popup.adjustSize()
    popup.show()
    settle(300)
    popup.grab().save(str(OUT / "popup.png"))
    popup.close()
    window.hide()
    engine.stop()
    rt.close()
    print(f"wrote screenshots to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
