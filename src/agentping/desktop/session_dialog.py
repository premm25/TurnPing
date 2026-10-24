"""Fallback when AgentPing cannot focus a session's window automatically."""

from __future__ import annotations

import shutil

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from agentping.adapters.registry import default_registry
from agentping.core.session_manager import SessionState
from agentping.desktop.controller import SessionDetails
from agentping.platform import windows as win


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setWordWrap(True)
    return lbl


class SessionDialog(QDialog):
    def __init__(self, details: SessionDetails, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Return to session")
        self.setMinimumWidth(480)
        session, event = details.session, details.event
        provider = session.provider if session else event.provider if event else "unknown"
        project = (session.project_path if session else None) or (
            event.project_path if event else None
        )
        session_id = (session.session_id if session else None) or (
            event.session_id if event else None
        )
        host = session.host if session else None

        layout = QVBoxLayout(self)
        title = QLabel(f"{default_registry().display_name(provider)} session")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        why = _label(f"AgentPing could not bring this session's window forward: {details.reason}.")
        why.setProperty("muted", True)
        layout.addWidget(why)

        form = QFormLayout()
        form.addRow("Project:", _label(project or "not reported by the agent"))
        form.addRow(
            "Session id:", _label(session_id or "not reported (sessions grouped by folder)")
        )
        if session:
            form.addRow("State:", _label(SessionState(session.state).label))
        if event:
            form.addRow(
                "Event:", _label(f"{event.category} at {event.received_at.astimezone():%H:%M:%S}")
            )
            if event.message:
                form.addRow("Message:", _label(event.message))
        where = "unknown"
        if host:
            where = host.title or host.process_name or host.terminal or "unknown"
            if host.process_name and host.title:
                where = f"{host.title} ({host.process_name})"
        form.addRow("Window:", _label(where))
        layout.addLayout(form)

        how = _label(
            "To return manually, switch to the window above (Alt+Tab) and select the terminal "
            "tab running this agent. AgentPing can focus a window but not a specific tab."
        )
        how.setProperty("muted", True)
        layout.addWidget(how)

        row = QHBoxLayout()
        if project:
            open_folder = QPushButton("Open folder")
            open_folder.clicked.connect(lambda: win.open_folder(project))
            row.addWidget(open_folder)
            if shutil.which("code"):
                vscode = QPushButton("Open in VS Code")
                vscode.clicked.connect(lambda: win.open_in_vscode(project))
                row.addWidget(vscode)
            copy = QPushButton("Copy path")
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(project))
            row.addWidget(copy)
        if session_id:
            copy_id = QPushButton("Copy session id")
            copy_id.clicked.connect(lambda: QGuiApplication.clipboard().setText(session_id))
            row.addWidget(copy_id)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
