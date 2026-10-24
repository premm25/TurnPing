"""Settings page. Every control maps to a validated config field that the engine reads."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from turnping import APP_NAME
from turnping.config import AppConfig, EventRule, ProviderConfig
from turnping.contracts.events import EventCategory, Priority
from turnping.desktop.controller import Controller
from turnping.desktop.widgets import plain

PROVIDERS = [("claude-code", "Claude Code"), ("codex", "Codex CLI"), ("gemini-cli", "Gemini CLI")]


def _check(text: str, tip: str) -> QCheckBox:
    box = QCheckBox(text)
    box.setToolTip(tip)
    return box


def _spin(lo: int, hi: int, suffix: str, tip: str) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSuffix(suffix)
    s.setToolTip(tip)
    return s


class SettingsPage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(plain("Settings", obj="PageTitle"))
        self.error_label = plain("", wrap=True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # General
        g = QGroupBox("General")
        f = QFormLayout(g)
        self.enabled = _check(
            "Show notifications", "Master switch. When off, events are still recorded in history."
        )
        self.backend = QComboBox()
        self.backend.addItem(
            "Automatic (Windows toasts, TurnPing popups if toasts are off)", "auto"
        )
        self.backend.addItem("Windows toast notifications only", "toast")
        self.backend.addItem("TurnPing popups only", "popup")
        self.backend.setToolTip("How notifications are displayed")
        self.close_to_tray = _check(
            "Keep running in the tray when the window is closed",
            "Reminders and popups need TurnPing running. Use Exit in the tray menu to quit.",
        )
        self.start_with_windows = _check(
            "Start TurnPing when I sign in to Windows",
            "Adds a per-user entry under HKCU\\...\\Run. No admin rights needed.",
        )
        self.launch_on_event = _check(
            "Start TurnPing automatically when an agent sends an event",
            "If the app isn't running, the hook starts it in the background.",
        )
        self.show_dashboard = _check(
            "Open the dashboard when TurnPing starts (unless started in the background)",
            "Background starts (sign-in, hooks) always stay in the tray.",
        )
        f.addRow(self.enabled)
        f.addRow("Display:", self.backend)
        f.addRow(self.close_to_tray)
        f.addRow(self.start_with_windows)
        f.addRow(self.launch_on_event)
        f.addRow(self.show_dashboard)
        layout.addWidget(g)

        # Events
        g = QGroupBox("Events")
        grid = QGridLayout(g)
        grid.addWidget(plain("Event", muted=True), 0, 0)
        grid.addWidget(plain("Notify", muted=True), 0, 1)
        grid.addWidget(plain("Priority", muted=True), 0, 2)
        grid.addWidget(plain("Sound", muted=True), 0, 3)
        self.rules: dict[str, tuple[QCheckBox, QComboBox, QCheckBox]] = {}
        tips = {
            EventCategory.RESPONSE_STOPPED: "The agent finished its response and is waiting for your next prompt.",
            EventCategory.TASK_COMPLETED: (
                "Only sent by integrations that report real task completion (e.g. custom scripts)."
            ),
            EventCategory.PERMISSION_NEEDED: "The agent is blocked until you approve or deny an action.",
            EventCategory.INPUT_NEEDED: "The agent asked you a question or is waiting for input.",
            EventCategory.TASK_FAILED: "The agent reported a failure, e.g. an API error ended the turn.",
            EventCategory.SESSION_RESUMED: "Activity after a pause. Normally only updates session state.",
            EventCategory.SESSION_ENDED: "The agent session exited.",
        }
        for row, cat in enumerate(
            [c for c in EventCategory if c is not EventCategory.UNKNOWN], start=1
        ):
            label = plain(cat.label)
            label.setToolTip(tips.get(cat, ""))
            notify = QCheckBox()
            notify.setAccessibleName(f"Notify for {cat.label}")
            prio = QComboBox()
            prio.setAccessibleName(f"Priority for {cat.label}")
            for p in Priority:
                prio.addItem(p.value.title(), p.value)
            prio.setToolTip("Urgent and high stay on screen longer; urgent stays until dismissed")
            sound = QCheckBox()
            sound.setAccessibleName(f"Sound for {cat.label}")
            grid.addWidget(label, row, 0)
            grid.addWidget(notify, row, 1, alignment=Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(prio, row, 2)
            grid.addWidget(sound, row, 3, alignment=Qt.AlignmentFlag.AlignCenter)
            self.rules[cat.value] = (notify, prio, sound)
        grid.setColumnStretch(0, 1)
        layout.addWidget(g)

        # Sound and quiet hours
        g = QGroupBox("Sound and quiet hours")
        f = QFormLayout(g)
        self.sound = _check("Play sounds (uses your Windows sound scheme)", "Master sound switch")
        self.quiet = _check("Enable quiet hours", "Suppress notifications during these hours")
        self.quiet_start = QTimeEdit()
        self.quiet_start.setDisplayFormat("HH:mm")
        self.quiet_end = QTimeEdit()
        self.quiet_end.setDisplayFormat("HH:mm")
        hours = QHBoxLayout()
        hours.addWidget(self.quiet_start)
        hours.addWidget(plain("to"))
        hours.addWidget(self.quiet_end)
        hours.addStretch(1)
        self.quiet_urgent = _check(
            "Still show high-priority alerts (silently) during quiet hours",
            "Permission and input alerts keep agents unblocked",
        )
        f.addRow(self.sound)
        f.addRow(self.quiet)
        f.addRow("Quiet from:", hours)
        f.addRow(self.quiet_urgent)
        layout.addWidget(g)

        # Reminders
        g = QGroupBox("Reminders")
        f = QFormLayout(g)
        self.rem_enabled = _check(
            "Remind me when an agent is still waiting for permission or input",
            "Stops as soon as the session shows activity, finishes, ends, or you open/dismiss the alert",
        )
        self.rem_delay = _spin(1, 1440, " min", "First reminder after this long without a response")
        self.rem_repeat = _spin(1, 1440, " min", "Time between further reminders")
        self.rem_max = _spin(0, 20, "", "Maximum reminders per alert (0 disables)")
        self.rem_perm = _check("Permission requests", "")
        self.rem_input = _check("Input requests", "")
        self.rem_dismiss = _check("Dismissing an alert stops its reminders", "")
        f.addRow(self.rem_enabled)
        f.addRow("First reminder after:", self.rem_delay)
        f.addRow("Repeat every:", self.rem_repeat)
        f.addRow("At most:", self.rem_max)
        cats = QHBoxLayout()
        cats.addWidget(self.rem_perm)
        cats.addWidget(self.rem_input)
        cats.addStretch(1)
        f.addRow("For:", cats)
        f.addRow(self.rem_dismiss)
        f.addRow(
            plain(
                "Gemini CLI does not expose a reliable 'resumed' signal, so reminders are not "
                "sent for it.",
                muted=True,
                wrap=True,
            )
        )
        layout.addWidget(g)

        # Providers
        g = QGroupBox("Agents")
        f = QFormLayout(g)
        self.providers: dict[str, QCheckBox] = {}
        for pid, name in PROVIDERS:
            box = _check(f"Notify for {name}", f"Turn off to ignore all {name} events")
            self.providers[pid] = box
            f.addRow(box)
        self.claude_idle = _check(
            "Claude Code: also alert when Claude has been idle ~60 s after responding",
            "Uses Claude Code's idle_prompt notification. Updates the installed hook when saved.",
        )
        f.addRow(self.claude_idle)
        layout.addWidget(g)

        # Privacy & history
        g = QGroupBox("Privacy and history")
        f = QFormLayout(g)
        self.preview = _check(
            "Include a short preview of the agent's last message in notifications",
            "Off by default: notifications (and Windows' Action Center) then never contain agent output",
        )
        self.show_path = _check("Show the project folder name in notifications", "")
        self.retention = _spin(1, 3650, " days", "Older history is deleted automatically")
        self.max_events = _spin(
            100, 1_000_000, " events", "History is trimmed to this many entries"
        )
        self.max_events.setSingleStep(500)
        f.addRow(self.preview)
        f.addRow(self.show_path)
        f.addRow("Keep history for:", self.retention)
        f.addRow("Keep at most:", self.max_events)
        layout.addWidget(g)

        # Diagnostics
        g = QGroupBox("Diagnostics")
        f = QFormLayout(g)
        self.log_level = QComboBox()
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level.addItem(lvl.title(), lvl)
        self.log_level.setToolTip("Logs never contain prompts, tool input or secrets")
        f.addRow("Log level:", self.log_level)
        folders = QHBoxLayout()
        logs = QPushButton("Open logs folder")
        logs.clicked.connect(lambda: self._open(controller.runtime.paths.log_dir))
        data = QPushButton("Open data folder")
        data.clicked.connect(lambda: self._open(controller.runtime.paths.home))
        folders.addWidget(logs)
        folders.addWidget(data)
        folders.addStretch(1)
        f.addRow(folders)
        layout.addWidget(g)

        buttons = QHBoxLayout()
        defaults = QPushButton("Restore defaults")
        defaults.setToolTip("Fill the form with default values (not saved until you click Save)")
        defaults.clicked.connect(lambda: self.load(AppConfig()))
        revert = QPushButton("Revert")
        revert.setToolTip("Discard unsaved changes")
        revert.clicked.connect(self.reload)
        self.save_btn = QPushButton("Save")
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self.save)
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        buttons.addWidget(revert)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)
        layout.addStretch(1)

        self.reload()

    @staticmethod
    def _open(path: os.PathLike) -> None:
        from turnping.platform.windows import open_folder

        open_folder(str(path))

    # ----- form <-> config ------------------------------------------------------------

    def reload(self) -> None:
        cfg = self.controller.config
        self.load(cfg)
        error = self.controller.runtime.config_result.error
        if error:
            self._error(
                f"Your config file is invalid ({error}). Defaults are shown; saving replaces the "
                "file and keeps a copy of the broken one."
            )
        else:
            self._error(None)
        self.controller.start_with_windows(self.start_with_windows.setChecked)

    def load(self, cfg: AppConfig) -> None:
        self.enabled.setChecked(cfg.general.enabled)
        self.backend.setCurrentIndex(
            max(0, self.backend.findData(cfg.general.notification_backend))
        )
        self.close_to_tray.setChecked(cfg.general.close_to_tray)
        self.launch_on_event.setChecked(cfg.general.launch_app_on_event)
        self.show_dashboard.setChecked(cfg.general.show_dashboard_on_start)
        for key, (notify, prio, sound) in self.rules.items():
            rule = cfg.events.get(key) or EventRule()
            notify.setChecked(rule.notify)
            prio.setCurrentIndex(max(0, prio.findData(rule.priority.value)))
            sound.setChecked(rule.sound)
        self.sound.setChecked(cfg.sound.enabled)
        self.quiet.setChecked(cfg.quiet_hours.enabled)
        self.quiet_start.setTime(QTime.fromString(cfg.quiet_hours.start, "HH:mm"))
        self.quiet_end.setTime(QTime.fromString(cfg.quiet_hours.end, "HH:mm"))
        self.quiet_urgent.setChecked(cfg.quiet_hours.allow_urgent)
        r = cfg.reminders
        self.rem_enabled.setChecked(r.enabled)
        self.rem_delay.setValue(max(1, round(r.initial_delay_seconds / 60)))
        self.rem_repeat.setValue(max(1, round(r.repeat_interval_seconds / 60)))
        self.rem_max.setValue(r.max_reminders)
        self.rem_perm.setChecked(EventCategory.PERMISSION_NEEDED in r.categories)
        self.rem_input.setChecked(EventCategory.INPUT_NEEDED in r.categories)
        self.rem_dismiss.setChecked(r.stop_on_dismiss)
        for pid, box in self.providers.items():
            box.setChecked(cfg.provider(pid).enabled)
        self.claude_idle.setChecked(cfg.provider("claude-code").notify_idle_prompt)
        self.preview.setChecked(cfg.privacy.include_message_preview)
        self.show_path.setChecked(cfg.privacy.show_project_path)
        self.retention.setValue(cfg.history.retention_days)
        self.max_events.setValue(cfg.history.max_events)
        self.log_level.setCurrentIndex(max(0, self.log_level.findData(cfg.general.log_level)))

    def to_config(self) -> AppConfig:
        base = self.controller.config.model_dump(mode="json")
        base["general"].update(
            enabled=self.enabled.isChecked(),
            notification_backend=self.backend.currentData(),
            close_to_tray=self.close_to_tray.isChecked(),
            launch_app_on_event=self.launch_on_event.isChecked(),
            show_dashboard_on_start=self.show_dashboard.isChecked(),
            log_level=self.log_level.currentData(),
        )
        base["events"] = {
            key: {"notify": n.isChecked(), "priority": p.currentData(), "sound": s.isChecked()}
            for key, (n, p, s) in self.rules.items()
        } | {
            "UNKNOWN": base["events"].get(
                "UNKNOWN", {"notify": False, "priority": "low", "sound": False}
            )
        }
        base["sound"] = {"enabled": self.sound.isChecked()}
        base["quiet_hours"] = {
            "enabled": self.quiet.isChecked(),
            "start": self.quiet_start.time().toString("HH:mm"),
            "end": self.quiet_end.time().toString("HH:mm"),
            "allow_urgent": self.quiet_urgent.isChecked(),
        }
        categories = []
        if self.rem_perm.isChecked():
            categories.append("PERMISSION_NEEDED")
        if self.rem_input.isChecked():
            categories.append("INPUT_NEEDED")
        base["reminders"] = {
            "enabled": self.rem_enabled.isChecked(),
            "initial_delay_seconds": self.rem_delay.value() * 60,
            "repeat_interval_seconds": self.rem_repeat.value() * 60,
            "max_reminders": self.rem_max.value(),
            "categories": categories,
            "stop_on_dismiss": self.rem_dismiss.isChecked(),
        }
        providers = dict(base.get("providers") or {})
        for pid, box in self.providers.items():
            current = ProviderConfig.model_validate(providers.get(pid, {}))
            providers[pid] = current.model_copy(update={"enabled": box.isChecked()}).model_dump()
        providers["claude-code"]["notify_idle_prompt"] = self.claude_idle.isChecked()
        base["providers"] = providers
        base["privacy"] = {
            "include_message_preview": self.preview.isChecked(),
            "show_project_path": self.show_path.isChecked(),
        }
        base["history"] = {
            "retention_days": self.retention.value(),
            "max_events": self.max_events.value(),
        }
        return AppConfig.model_validate(base)

    def save(self) -> None:
        try:
            config = self.to_config()
        except ValueError as exc:
            self._error(f"Not saved: {exc}")
            return
        self.save_btn.setEnabled(False)

        def done(error: str | None) -> None:
            self.save_btn.setEnabled(True)
            if error:
                self._error(f"Not saved: {error}")
                QMessageBox.warning(self, APP_NAME, f"Settings were not saved.\n\n{error}")
            else:
                self._error(None)
                self.controller.status_message.emit("Settings saved.")

        self.controller.save_config(
            config, start_with_windows=self.start_with_windows.isChecked(), on_done=done
        )

    def _error(self, text: str | None) -> None:
        if text:
            self.error_label.setText(text)
            self.error_label.setStyleSheet("color: #C93636; font-weight: 600;")
            self.error_label.show()
        else:
            self.error_label.hide()
