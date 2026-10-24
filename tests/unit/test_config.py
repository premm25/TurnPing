from datetime import time

import pytest
from pydantic import ValidationError

from agentping.config import (
    AppConfig,
    QuietHoursConfig,
    RemindersConfig,
    load_config,
    quarantine_broken_config,
    save_config,
)
from agentping.contracts.events import EventCategory, Priority


def test_defaults_are_valid_and_complete():
    cfg = AppConfig()
    for c in EventCategory:
        assert c.value in cfg.events
    assert cfg.rule(EventCategory.PERMISSION_NEEDED).priority is Priority.URGENT
    assert not cfg.rule(EventCategory.SESSION_RESUMED).notify
    assert cfg.privacy.include_message_preview is False


def test_missing_file_uses_defaults(tmp_path):
    result = load_config(tmp_path / "none.toml")
    assert not result.existed and result.error is None
    assert result.config == AppConfig()


def test_save_and_load_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.general.notification_backend = "popup"
    cfg.quiet_hours.enabled = True
    path = save_config(cfg, tmp_path / "config.toml")
    loaded = load_config(path)
    assert loaded.error is None
    assert loaded.config == cfg


def test_partial_file_merges_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[events.RESPONSE_STOPPED]\nnotify = false\n", encoding="utf-8")
    cfg = load_config(path).config
    assert cfg.rule(EventCategory.RESPONSE_STOPPED).notify is False
    assert cfg.rule(EventCategory.PERMISSION_NEEDED).notify is True


@pytest.mark.parametrize(
    "text",
    [
        "this is = = not toml",
        "[general]\nunknown_key = 1\n",
        "[general]\nnotification_backend = 'carrier-pigeon'\n",
        "[history]\nretention_days = 0\n",
        "[events.NOPE]\nnotify = true\n",
        "[quiet_hours]\nstart = '25:00'\n",
    ],
)
def test_invalid_files_report_error_and_fall_back(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    result = load_config(path)
    assert result.error
    assert result.config == AppConfig()
    assert path.read_text(encoding="utf-8") == text  # never overwritten silently


def test_quarantine_copies_broken_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("broken", encoding="utf-8")
    moved = quarantine_broken_config(path)
    assert moved is not None and moved.read_text(encoding="utf-8") == "broken"


def test_quiet_hours_wrapping_midnight():
    q = QuietHoursConfig(enabled=True, start="22:00", end="07:00")
    assert q.contains(time(23, 30))
    assert q.contains(time(3, 0))
    assert not q.contains(time(7, 0))
    assert not q.contains(time(12, 0))


def test_quiet_hours_same_day_and_empty():
    q = QuietHoursConfig(start="09:00", end="17:00")
    assert q.contains(time(9, 0)) and not q.contains(time(17, 0))
    assert not QuietHoursConfig(start="09:00", end="09:00").contains(time(9, 0))


def test_reminders_only_attention_categories():
    with pytest.raises(ValidationError):
        RemindersConfig(categories=[EventCategory.RESPONSE_STOPPED])


def test_invalid_provider_id_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"providers": {"Bad Id": {}}})
