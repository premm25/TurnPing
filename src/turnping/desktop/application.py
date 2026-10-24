"""Desktop application bootstrap: single instance, IPC, engine, tray, windows."""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

from turnping import APP_ID, APP_NAME, __version__

log = logging.getLogger(__name__)


def _forward_to_running_instance(show_event: str | None, background: bool) -> bool:
    from turnping.ingest.ipc import send_message

    try:
        if show_event:
            send_message({"type": "command", "command": "open_event", "event_id": show_event})
        elif not background:
            send_message({"type": "command", "command": "show"})
        return True
    except ConnectionError:
        return False


def run_app(*, background: bool = False, show_event: str | None = None) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from turnping.contracts.events import AgentEvent
    from turnping.core.engine import Engine
    from turnping.desktop import theme
    from turnping.desktop.controller import Controller, SessionDetails
    from turnping.desktop.popup import PopupBackend, PopupBridge, PopupManager
    from turnping.desktop.tray import Tray
    from turnping.ingest.ipc import AlreadyRunningError, IpcServer
    from turnping.ingest.spool import Spool
    from turnping.installation.system import register_system_integration
    from turnping.notifications.windows_toast import WindowsToastBackend
    from turnping.runtime import build_runtime, setup_logging
    from turnping.security.paths import get_paths

    paths = get_paths().ensure()
    setup_logging(paths, "INFO")

    if sys.platform == "win32":
        import ctypes

        # Group our windows/taskbar button under the same identity as our toasts.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    app = cast(QApplication, QApplication.instance() or QApplication(sys.argv[:1]))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setWindowIcon(theme.app_icon())

    # Single instance: whoever binds the pipe first is the app; others forward and exit.
    pending: dict[str, Any] = {}

    def early_handler(message: dict[str, Any]) -> dict[str, Any]:
        handler = pending.get("handler")
        if handler is None:
            return {"ok": False, "error": "starting"}
        return handler(message)

    server = IpcServer(early_handler)
    try:
        server.start()
    except AlreadyRunningError:
        log.info("TurnPing already running; forwarding request")
        _forward_to_running_instance(show_event, background)
        return 0

    runtime = build_runtime(paths=paths)
    setup_logging(paths, runtime.config.general.log_level)
    log.info("TurnPing %s starting (background=%s)", __version__, background)
    try:
        register_system_integration()
    except OSError:
        log.exception("system registration failed")

    bridge = PopupBridge()
    popup_backend = PopupBackend(bridge)
    runtime.notifier.add_backend("popup", popup_backend)
    popups = PopupManager(bridge)

    engine = Engine(runtime.router, spool=Spool(paths.spool_dir))
    controller = Controller(runtime, engine)

    toast = runtime.notifier.backend("toast")
    if isinstance(toast, WindowsToastBackend):
        toast.on_dismissed = lambda event_id, reason: (
            controller.acknowledge(event_id, dismissed=True) if reason == "user" else None
        )

    from turnping.desktop.main_window import MainWindow
    from turnping.desktop.session_dialog import SessionDialog

    app.setStyleSheet(theme.stylesheet(theme.current()))
    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda _s: app.setStyleSheet(theme.stylesheet(theme.current()))
        )
    except AttributeError:
        pass

    window = MainWindow(controller)
    tray = Tray()
    tray_ok = tray.show()
    if not tray_ok:
        log.warning("system tray unavailable; closing the window will exit TurnPing")

    def show_details(details: SessionDetails) -> None:
        dialog = SessionDialog(details, window if window.isVisible() else None)
        dialog.setWindowIcon(theme.app_icon())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    controller.session_details_requested.connect(show_details)
    controller.status_message.connect(lambda text: window.statusBar().showMessage(text, 6000))
    popups.open_event.connect(controller.open_event)
    popups.dismiss_event.connect(lambda eid: controller.acknowledge(eid, dismissed=True))

    def update_tray(*_args: Any) -> None:
        tray.set_state(paused=controller.paused, attention=len(runtime.router.reminders.pending()))

    controller.paused_changed.connect(update_tray)
    controller.event_processed.connect(update_tray)
    tray.open_page.connect(window.show_page)
    controller.show_page_requested.connect(window.show_page)
    tray.pause_toggled.connect(controller.set_paused)
    tray.integrations_requested.connect(lambda: window.show_page("dashboard"))

    def tray_test() -> None:
        def done(result: Any) -> None:
            if result is None or result.delivery is None or not result.delivery.status.delivered:
                detail = result.delivery.detail if result and result.delivery else "not processed"
                tray.icon.showMessage(APP_NAME, f"Test notification could not be shown: {detail}")

        controller.send_test("TASK_COMPLETED", done)

    tray.test_requested.connect(tray_test)

    # ----- IPC handling (runs on IPC worker threads) ----------------------------------

    def handle(message: dict[str, Any]) -> dict[str, Any]:
        kind = message.get("type")
        if kind == "event":
            try:
                event = AgentEvent.from_wire(message.get("event"))
            except ValueError:
                return {"ok": False, "error": "invalid event"}
            return {"ok": controller.submit_event(event)}
        if kind == "test_notification":
            try:
                event = AgentEvent.from_wire(message.get("event"))
            except ValueError:
                return {"ok": False, "error": "invalid event"}
            result = controller.process_and_wait(event)
            if result is None:
                return {"ok": False, "error": "timed out"}
            return {
                "ok": True,
                "decision": result.decision.action.value,
                "reason": result.decision.reason,
                "delivery_status": result.delivery.status.value if result.delivery else None,
                "delivery_detail": result.delivery.detail if result.delivery else None,
            }
        if kind == "command":
            command = message.get("command")
            event_id = message.get("event_id")
            if command == "ping":
                return {"ok": True, "version": __version__}
            if command == "show":
                controller.show_page_requested.emit("dashboard")  # queued to the GUI thread
                return {"ok": True}
            if (
                command in ("open_event", "acknowledge")
                and isinstance(event_id, str)
                and len(event_id) <= 64
            ):
                if command == "acknowledge":
                    controller.acknowledge(event_id, dismissed=False)
                else:
                    controller.show_event_details(
                        event_id, "the window could not be focused automatically"
                    )
                return {"ok": True}
            if command == "quit":
                controller.quit_requested.emit()  # queued to the GUI thread
                return {"ok": True}
            if command in ("pause", "resume"):
                controller.set_paused(command == "pause")
                return {"ok": True}
            return {"ok": False, "error": "unknown command"}
        return {"ok": False, "error": "unknown message type"}

    pending["handler"] = handle
    engine.start()

    # ----- shutdown -------------------------------------------------------------------

    shutting_down = {"done": False}

    def shutdown() -> None:
        if shutting_down["done"]:
            return
        shutting_down["done"] = True
        log.info("TurnPing shutting down")
        popup_backend.enabled = False
        popups.close_all()
        tray.hide()
        server.stop()
        engine.stop()
        runtime.close()

    def quit_app() -> None:
        shutdown()
        app.quit()

    tray.quit_requested.connect(quit_app)
    controller.quit_requested.connect(quit_app)
    window.quit_requested.connect(quit_app)
    app.aboutToQuit.connect(shutdown)

    update_tray()
    if show_event:
        QTimer.singleShot(
            300, lambda: controller.show_event_details(show_event, "opened from a notification")
        )
    if (
        not background and (runtime.config.general.show_dashboard_on_start or not tray_ok)
    ) or not tray_ok:
        window.show_page("dashboard")

    try:
        paths.launch_lock_file.unlink(missing_ok=True)
    except OSError:
        pass
    return app.exec()
