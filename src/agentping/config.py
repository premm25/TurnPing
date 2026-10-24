"""Validated TOML configuration.

The file lives at ``<AgentPing home>/config.toml``. Missing keys take documented
defaults; unknown keys are rejected so typos surface in ``agentping doctor``. A file
that fails to parse is never overwritten silently: AgentPing runs on defaults, reports
the error, and the settings window refuses to save until the user confirms a reset
(which first copies the broken file aside).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tomllib
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agentping.contracts.events import EventCategory, Priority
from agentping.security.paths import get_paths

log = logging.getLogger(__name__)

C = EventCategory


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeneralConfig(_Section):
    enabled: bool = True
    notification_backend: Literal["auto", "toast", "popup"] = "auto"
    close_to_tray: bool = True
    launch_app_on_event: bool = True
    show_dashboard_on_start: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class PrivacyConfig(_Section):
    include_message_preview: bool = False
    show_project_path: bool = True


class HistoryConfig(_Section):
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_events: int = Field(default=5000, ge=100, le=1_000_000)


class SoundConfig(_Section):
    enabled: bool = True


class QuietHoursConfig(_Section):
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    allow_urgent: bool = True  # permission/input alerts still show (silently)

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("expected HH:MM (24-hour)")
        return v

    def contains(self, moment: time) -> bool:
        start = time.fromisoformat(self.start)
        end = time.fromisoformat(self.end)
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end  # wraps past midnight


class EventRule(_Section):
    notify: bool = True
    priority: Priority = Priority.NORMAL
    sound: bool = False


def _default_rules() -> dict[str, EventRule]:
    return {
        C.RESPONSE_STOPPED.value: EventRule(notify=True, priority=Priority.NORMAL, sound=True),
        C.TASK_COMPLETED.value: EventRule(notify=True, priority=Priority.NORMAL, sound=True),
        C.PERMISSION_NEEDED.value: EventRule(notify=True, priority=Priority.URGENT, sound=True),
        C.INPUT_NEEDED.value: EventRule(notify=True, priority=Priority.HIGH, sound=True),
        C.TASK_FAILED.value: EventRule(notify=True, priority=Priority.HIGH, sound=True),
        C.SESSION_RESUMED.value: EventRule(notify=False, priority=Priority.LOW, sound=False),
        C.SESSION_ENDED.value: EventRule(notify=False, priority=Priority.LOW, sound=False),
        C.UNKNOWN.value: EventRule(notify=False, priority=Priority.LOW, sound=False),
    }


class RemindersConfig(_Section):
    enabled: bool = True
    initial_delay_seconds: int = Field(default=180, ge=30, le=86_400)
    repeat_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    max_reminders: int = Field(default=2, ge=0, le=20)
    categories: list[EventCategory] = Field(
        default_factory=lambda: [C.PERMISSION_NEEDED, C.INPUT_NEEDED]
    )
    stop_on_dismiss: bool = True

    @field_validator("categories")
    @classmethod
    def _only_attention(cls, v: list[EventCategory]) -> list[EventCategory]:
        bad = [c for c in v if not c.needs_attention]
        if bad:
            raise ValueError("reminders only apply to PERMISSION_NEEDED and INPUT_NEEDED")
        return list(dict.fromkeys(v))


class ProviderConfig(_Section):
    enabled: bool = True
    notify_idle_prompt: bool = False  # Claude Code only: alert ~60 s after a response


class DedupConfig(_Section):
    window_seconds: int = Field(default=10, ge=0, le=600)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    config_version: int = 1
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    sound: SoundConfig = Field(default_factory=SoundConfig)
    quiet_hours: QuietHoursConfig = Field(default_factory=QuietHoursConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    events: dict[str, EventRule] = Field(default_factory=_default_rules)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_rules(self) -> AppConfig:
        defaults = _default_rules()
        for key in self.events:
            if key not in defaults:
                raise ValueError(f"unknown event category in [events]: {key}")
        merged = {**defaults, **self.events}
        if merged != self.events:
            object.__setattr__(self, "events", merged)
        for key in self.providers:
            if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,39}", key):
                raise ValueError(f"invalid provider id in [providers]: {key}")
        return self

    def rule(self, category: EventCategory) -> EventRule:
        return self.events.get(category.value) or _default_rules()[category.value]

    def provider(self, provider_id: str) -> ProviderConfig:
        return self.providers.get(provider_id) or ProviderConfig()


class ConfigLoadResult(BaseModel):
    config: AppConfig
    path: Path
    existed: bool
    error: str | None = None


def _serialize(config: AppConfig) -> dict[str, object]:
    return config.model_dump(mode="json", exclude_none=True)


def load_config(path: Path | None = None) -> ConfigLoadResult:
    path = path or get_paths().config_file
    if not path.exists():
        return ConfigLoadResult(config=AppConfig(), path=path, existed=False)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return ConfigLoadResult(config=AppConfig.model_validate(data), path=path, existed=True)
    except (tomllib.TOMLDecodeError, ValidationError, OSError, ValueError) as exc:
        detail = _describe_error(exc)
        log.error("configuration file %s is invalid: %s", path, detail)
        return ConfigLoadResult(config=AppConfig(), path=path, existed=True, error=detail)


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg')}")
        return "; ".join(parts)
    return str(exc)[:300]


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    """Validate and atomically write the configuration."""
    path = path or get_paths().config_file
    AppConfig.model_validate(_serialize(config))  # round-trip validation
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    header = (
        "# AgentPing configuration. See docs/CONFIGURATION.md for every option.\n"
        "# Edited by the Settings window; manual edits are fine too.\n\n"
    )
    tmp.write_text(header + tomli_w.dumps(_serialize(config)), encoding="utf-8")
    os.replace(tmp, path)
    return path


def quarantine_broken_config(path: Path | None = None) -> Path | None:
    """Move an unparseable config aside so it can be recovered manually."""
    path = path or get_paths().config_file
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"config.broken-{stamp}.toml")
    shutil.copy2(path, target)
    return target
