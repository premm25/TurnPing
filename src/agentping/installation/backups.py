"""Timestamped backups of agent configuration files before AgentPing edits them."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

KEEP_BACKUPS = 20


def backup_file(source: Path, backup_root: Path, provider: str) -> Path:
    target_dir = backup_root / provider
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_dir / f"{source.name}.{stamp}.bak"
    shutil.copy2(source, target)
    _prune(target_dir, source.name)
    return target


def _prune(directory: Path, name: str) -> None:
    backups = sorted(directory.glob(f"{name}.*.bak"))
    for old in backups[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


def list_backups(backup_root: Path, provider: str) -> list[Path]:
    directory = backup_root / provider
    return sorted(directory.glob("*.bak")) if directory.exists() else []


def restore(backup: Path, target: Path) -> None:
    shutil.copy2(backup, target)
