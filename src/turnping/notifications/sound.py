"""Non-blocking notification sounds using the user's Windows sound scheme."""

from __future__ import annotations

import logging
import sys

from turnping.contracts.events import Priority

log = logging.getLogger(__name__)

# Windows system sound aliases; they follow the user's sound scheme (and mute setting).
_ALIASES = {
    Priority.LOW: "SystemAsterisk",
    Priority.NORMAL: "SystemAsterisk",
    Priority.HIGH: "SystemExclamation",
    Priority.URGENT: "SystemExclamation",
}


class SoundPlayer:
    def play(self, priority: Priority) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winsound

            winsound.PlaySound(
                _ALIASES.get(priority, "SystemAsterisk"),
                winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return True
        except Exception as exc:  # sound is best effort, never fatal
            log.debug("sound failed: %s", exc)
            return False


class SilentPlayer(SoundPlayer):
    def __init__(self) -> None:
        self.played: list[Priority] = []

    def play(self, priority: Priority) -> bool:
        self.played.append(priority)
        return True
