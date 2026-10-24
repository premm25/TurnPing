"""Desktop UI tests (Qt offscreen platform; OS toast mocked)."""

import threading

from agentping.config import load_config
from agentping.contracts.events import AgentEvent, EventCategory, Priority
from agentping.notifications.base import Notification


def ev(**kw):
    kw.setdefault("provider", "claude-code")
    kw.setdefault("native_type", "Notification:permission_prompt")
    kw.setdefault("category", EventCategory.PERMISSION_NEEDED)
    kw.setdefault("session_id", "s1")
    kw.setdefault("project_path", "C:/src/webapp")
    return AgentEvent(**kw)


def test_main_window_pages_and_dashboard_render(qtbot, stack):
    from agentping.desktop.main_window import MainWindow

    _runtime, engine, controller, toast, _ = stack
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show_page("dashboard")
    engine.submit(ev())
    qtbot.waitUntil(lambda: window.dashboard.sessions_table.rowCount() == 1, timeout=5000)
    assert window.dashboard.card_waiting.value.text() == "1"
    assert not window.dashboard.loading.isVisible()
    assert toast.shown  # the event reached the (mocked) notification backend
    window.show_page("history")
    qtbot.waitUntil(lambda: window.history.table.rowCount() == 1, timeout=5000)
    window.show_page("settings")
    assert window.stack.currentWidget() is window.settings


def test_closing_window_hides_to_tray_by_default(qtbot, stack):
    from agentping.desktop.main_window import MainWindow

    _, _, controller, _, _ = stack
    window = MainWindow(controller)
    qtbot.addWidget(window)
    quit_calls = []
    window.quit_requested.connect(lambda: quit_calls.append(1))
    window.show()
    window.close()
    assert not window.isVisible() and quit_calls == []


def test_pause_resume_through_controller(qtbot, stack):
    runtime, _, controller, _toast, _ = stack
    with qtbot.waitSignal(controller.paused_changed, timeout=3000):
        controller.set_paused(True)
    assert runtime.router.paused and controller.paused
    result = controller.process_and_wait(ev(session_id="paused"))
    assert result.decision.action.value == "suppressed"
    with qtbot.waitSignal(controller.paused_changed, timeout=3000):
        controller.set_paused(False)
    assert not runtime.router.paused


def test_settings_persist_and_apply(qtbot, stack, monkeypatch):
    from agentping.desktop.settings_window import SettingsPage

    runtime, _, controller, _, _ = stack
    monkeypatch.setattr(
        "agentping.installation.system.set_start_with_windows", lambda enabled: None
    )
    monkeypatch.setattr("agentping.installation.system.start_with_windows_enabled", lambda: False)
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    page.backend.setCurrentIndex(page.backend.findData("popup"))
    page.rules["RESPONSE_STOPPED"][0].setChecked(False)
    page.quiet.setChecked(True)
    page.rem_delay.setValue(7)
    page.providers["codex"].setChecked(False)
    with qtbot.waitSignal(controller.config_changed, timeout=5000):
        page.save()
    saved = load_config(runtime.paths.config_file)
    assert saved.error is None
    cfg = saved.config
    assert cfg.general.notification_backend == "popup"
    assert cfg.events["RESPONSE_STOPPED"].notify is False
    assert cfg.quiet_hours.enabled
    assert cfg.reminders.initial_delay_seconds == 420
    assert cfg.provider("codex").enabled is False
    assert runtime.config == cfg  # the running engine sees the new settings immediately
    # The form reloads the same values (persistence across restarts)
    page2 = SettingsPage(controller)
    qtbot.addWidget(page2)
    assert page2.backend.currentData() == "popup" and page2.rem_delay.value() == 7


def test_settings_restore_defaults_does_not_save_until_clicked(qtbot, stack):
    from agentping.desktop.settings_window import SettingsPage

    runtime, _, controller, _, _ = stack
    page = SettingsPage(controller)
    qtbot.addWidget(page)
    page.load(type(runtime.config)())
    assert not runtime.paths.config_file.exists()


def test_popup_fallback_shows_and_dismisses(qtbot, stack):
    _runtime, _, controller, toast, popups = stack
    toast.available = False  # Windows toasts turned off -> AgentPing popups
    result = controller.process_and_wait(ev(session_id="pop"))
    assert result.delivery.status.value == "dispatched"
    assert result.delivery.backend == "agentping-popup"
    qtbot.waitUntil(lambda: popups.visible_count == 1, timeout=3000)
    widget = popups._popups[0]
    with qtbot.waitSignal(popups.dismiss_event, timeout=2000) as blocker:
        widget._dismiss()
    assert blocker.args == [result.event.event_id]
    qtbot.waitUntil(lambda: popups.visible_count == 0, timeout=3000)


def test_popup_replaces_older_popup_for_same_session(qtbot, stack):
    _, _, _, _, popups = stack

    def note(i):
        return Notification(
            event_id=f"evt{i:06d}",
            title="t",
            body="b",
            attribution="a",
            priority=Priority.URGENT,
            category=EventCategory.PERMISSION_NEEDED,
            session_key="claude-code:s1",
        )

    popups.show_notification(note(1))
    popups.show_notification(note(2))
    qtbot.waitUntil(lambda: popups.visible_count == 1, timeout=3000)
    assert popups._popups[0].notification.event_id == "evt000002"


def test_popup_text_is_plain_not_html(qtbot, stack):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from agentping.desktop.popup import PopupWidget

    widget = PopupWidget(
        Notification(
            event_id="evt00001",
            title="<img src=x>",
            body="<b>hi</b>",
            attribution="a",
            priority=Priority.NORMAL,
            category=EventCategory.RESPONSE_STOPPED,
        )
    )
    qtbot.addWidget(widget)
    labels = [
        lbl for lbl in widget.findChildren(QLabel) if lbl.text() in ("<img src=x>", "<b>hi</b>")
    ]
    assert len(labels) == 2
    assert all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in labels)


def test_tray_state(qtbot):
    from agentping.desktop.tray import Tray

    tray = Tray()
    tray.set_state(paused=True, attention=0)
    assert tray.pause_action.text() == "Resume notifications"
    assert "paused" in tray.icon.toolTip()
    tray.set_state(paused=False, attention=2)
    assert "2 session(s)" in tray.icon.toolTip()
    emitted = []
    tray.pause_toggled.connect(emitted.append)
    tray.pause_action.trigger()
    assert emitted == [True]  # not paused -> request pause


def test_session_dialog_with_partial_data(qtbot, stack):
    from agentping.desktop.controller import SessionDetails
    from agentping.desktop.session_dialog import SessionDialog

    dialog = SessionDialog(SessionDetails(None, None, "no window was recorded"))
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Return to session"


def test_background_work_runs_off_gui_thread(qtbot):
    from agentping.desktop.workers import run_async

    seen = {}
    main = threading.current_thread()
    run_async(lambda: threading.current_thread(), lambda t: seen.setdefault("worker", t))
    qtbot.waitUntil(lambda: "worker" in seen, timeout=3000)
    assert seen["worker"] is not main


def test_send_test_notification_from_ui(qtbot, stack):
    _, _, controller, toast, _ = stack
    box = {}
    controller.send_test("TASK_COMPLETED", lambda r: box.setdefault("r", r))
    qtbot.waitUntil(lambda: "r" in box, timeout=5000)
    assert box["r"].delivery.status.value == "confirmed"
    assert toast.shown[-1].title == "AgentPing completed a task"


def test_theme_stylesheets_build():
    from agentping.desktop import theme

    assert "QPushButton" in theme.stylesheet(theme.LIGHT)
    assert "QPushButton" in theme.stylesheet(theme.DARK)
