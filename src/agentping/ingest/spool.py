"""On-disk spool for events produced while the desktop app is not reachable.

Hooks write already-normalized, validated events (never raw provider payloads) as small
JSON files. The app drains the spool on start-up and every few seconds. The spool is
bounded in file count and file size; anything invalid is discarded and logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

from agentping.contracts.events import AgentEvent

log = logging.getLogger(__name__)

MAX_FILES = 200
MAX_FILE_BYTES = 16 * 1024
MAX_AGE_SECONDS = 6 * 3600  # older spooled events are stale; do not alert on them


class Spool:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def put(self, event: AgentEvent) -> Path | None:
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.directory.glob("*.json"))
        if len(existing) >= MAX_FILES:
            log.warning("spool full (%d files); dropping event", len(existing))
            return None
        data = json.dumps(event.to_wire()).encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            return None
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
        tmp = self.directory / f"{name}.tmp"
        final = self.directory / f"{name}.json"
        tmp.write_bytes(data)
        os.replace(tmp, final)
        return final

    def drain(self) -> list[AgentEvent]:
        if not self.directory.exists():
            return []
        events: list[AgentEvent] = []
        now = time.time()
        for path in sorted(self.directory.glob("*.json")):
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("spool file too large")
                event = AgentEvent.from_wire(json.loads(path.read_bytes()))
                if now - event.timestamp.timestamp() <= MAX_AGE_SECONDS:
                    events.append(event)
                else:
                    log.info("discarding stale spooled event %s", event.event_id)
            except Exception as exc:
                log.warning(
                    "discarding invalid spool file %s: %s", path.name, exc.__class__.__name__
                )
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        for tmp in self.directory.glob("*.tmp"):
            if now - tmp.stat().st_mtime > 60:
                tmp.unlink(missing_ok=True)
        return events

    def count(self) -> int:
        return len(list(self.directory.glob("*.json"))) if self.directory.exists() else 0
