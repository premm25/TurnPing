"""Render a promotional image (LinkedIn / social preview) from the real UI.

Like scripts/screenshots.py, labelled demo events go through the real pipeline in a
throwaway data directory. Agent integrations are installed into a throwaway home folder,
and Windows toasts are reported as enabled, so the dashboard shows a typical working
setup instead of this machine's state. The image says "Demo data" in the corner.

    python scripts/promo_image.py            # assets/promo/turnping-linkedin.png
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "promo"
WIDTH, HEIGHT, SCALE = 1200, 627, 2  # LinkedIn's recommended 1.91:1, rendered at 2x


def main() -> int:
    os.environ["TURNPING_HOME"] = tempfile.mkdtemp(prefix="turnping-promo-")
    user_home = Path(tempfile.mkdtemp(prefix="turnping-promo-user-"))
    os.environ["TURNPING_USER_HOME"] = str(user_home)
    for folder in (".claude", ".codex", ".gemini"):
        (user_home / folder).mkdir()

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath
    from PySide6.QtWidgets import QApplication, QWidget

    from turnping.adapters.claude_code import ClaudeCodeAdapter
    from turnping.adapters.codex import CodexAdapter
    from turnping.adapters.gemini_cli import GeminiCliAdapter
    from turnping.contracts.events import EventCategory, HostWindow, Priority
    from turnping.core import health
    from turnping.core.engine import Engine
    from turnping.desktop import theme
    from turnping.desktop.controller import Controller
    from turnping.desktop.main_window import MainWindow
    from turnping.desktop.popup import PopupWidget
    from turnping.installation.hooks import INSTALLERS
    from turnping.notifications.base import (
        BackendStatus,
        DeliveryResult,
        DeliveryStatus,
        Notification,
        NotificationBackend,
    )
    from turnping.notifications.windows_toast import WindowsToastBackend
    from turnping.runtime import build_runtime

    class EnabledToast(WindowsToastBackend):
        def status(self) -> BackendStatus:
            return BackendStatus(True, "Windows notifications are enabled")

    class DemoToast(NotificationBackend):
        name = "windows-toast"

        def status(self) -> BackendStatus:
            return BackendStatus(True, "Windows notifications are enabled")

        def show(self, notification: Notification) -> DeliveryResult:
            return DeliveryResult(DeliveryStatus.CONFIRMED, self.name, "in Action Center")

    health.WindowsToastBackend = EnabledToast  # type: ignore[misc]

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet(theme.LIGHT))
    for installer in INSTALLERS.values():
        installer.install({})

    rt = build_runtime(toast=False)
    rt.notifier.add_backend("toast", DemoToast())
    code = HostWindow(hwnd=1, pid=1, process_name="code.exe", terminal="vscode")
    wt = HostWindow(hwnd=2, pid=2, process_name="windowsterminal.exe", terminal="windows-terminal")
    demo = [
        (GeminiCliAdapter(), {"hook_event_name": "AfterAgent", "session_id": "demo-g3",
                              "cwd": "C:/src/ml-pipeline"}, wt),
        (CodexAdapter(), {"hook_event_name": "Stop", "session_id": "demo-c7", "turn_id": "t1",
                          "cwd": "C:/src/payments-api"}, wt),
        (ClaudeCodeAdapter(), {"hook_event_name": "Stop", "session_id": "demo-a1",
                               "prompt_id": "1", "cwd": "C:/src/storefront"}, code),
        (ClaudeCodeAdapter(), {"hook_event_name": "Notification", "session_id": "demo-b2",
                               "prompt_id": "2", "cwd": "C:/src/storefront",
                               "notification_type": "permission_prompt",
                               "message": "Claude needs your permission to use Bash"}, code),
    ]  # fmt: skip
    for adapter, payload, host in demo:
        rt.router.process(adapter.translate(payload).model_copy(update={"host": host}))
        time.sleep(0.01)
    engine = Engine(rt.router)
    engine.start()
    window = MainWindow(Controller(rt, engine))
    window.resize(1180, 760)

    def sharp(widget: QWidget) -> QImage:
        """Render a widget at SCALE x so text stays crisp when placed on the canvas."""
        img = QImage(widget.size() * SCALE, QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(SCALE)
        img.fill(Qt.GlobalColor.transparent)
        widget.render(img)
        return img

    def settle(ms: int = 1200) -> None:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    window.show_page("dashboard")
    settle()
    dashboard = sharp(window)
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
    toast = sharp(popup)
    popup.close()
    window.hide()
    engine.stop()
    rt.close()

    image = QImage(WIDTH * SCALE, HEIGHT * SCALE, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(SCALE)
    p = QPainter(image)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

    bg = QLinearGradient(0, 0, WIDTH, HEIGHT)
    bg.setColorAt(0, QColor("#2B2373"))
    bg.setColorAt(1, QColor("#5446D9"))
    p.fillRect(QRectF(0, 0, WIDTH, HEIGHT), bg)

    def shadowed(img: QImage, rect: QRectF, radius: float) -> None:
        for i in range(14, 0, -2):  # soft drop shadow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 6, 40, 10))
            p.drawRoundedRect(rect.adjusted(-i, -i + 8, i, i + 8), radius + i, radius + i)
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(clip)
        p.drawImage(rect, img)
        p.restore()

    # Right: the dashboard, with the permission popup in front of it.
    dash_w = 610
    dash_rect = QRectF(WIDTH - dash_w - 40, 70, dash_w, dash_w * window.height() / window.width())
    shadowed(dashboard, dash_rect, 10)
    toast_w = 400
    toast_rect = QRectF(
        dash_rect.left() - 70, HEIGHT - 70 - toast_w * popup.height() / popup.width(),
        toast_w, toast_w * popup.height() / popup.width(),
    )  # fmt: skip
    shadowed(toast, toast_rect, 8)

    # Left: logo, name, pitch.
    p.drawPixmap(QRectF(56, 64, 64, 64), theme.draw_logo(128), QRectF(0, 0, 128, 128))
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI Semibold", 34))
    p.drawText(QPointF(136, 110), "TurnPing")
    p.setFont(QFont("Segoe UI Semibold", 25))
    p.drawText(QRectF(56, 160, 470, 120), Qt.TextFlag.TextWordWrap,
               "Know the moment your AI coding agent needs you.")  # fmt: skip
    p.setFont(QFont("Segoe UI", 14))
    p.setPen(QColor("#DCD8FF"))
    lines = [
        "Claude Code \u00b7 Codex CLI \u00b7 Gemini CLI",
        "Finished, failed or waiting for permission",
        "Click to jump back to the right window",
        "Free, open source, 100% local",
    ]
    for i, line in enumerate(lines):
        p.drawText(QPointF(56, 300 + i * 30), "\u2713  " + line)
    p.setFont(QFont("Segoe UI Semibold", 15))
    p.setPen(QColor("white"))
    p.drawText(QPointF(56, 450), "Windows 10/11")
    p.setFont(QFont("Segoe UI", 13))
    p.setPen(QColor("#DCD8FF"))
    p.drawText(QPointF(56, 478), "github.com/premm25/TurnPing")
    p.setFont(QFont("Segoe UI", 9))
    p.setPen(QColor(255, 255, 255, 150))
    p.drawText(QRectF(0, HEIGHT - 26, WIDTH - 14, 20), Qt.AlignmentFlag.AlignRight, "Demo data")
    p.end()

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "turnping-linkedin.png"
    image.save(str(target))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
