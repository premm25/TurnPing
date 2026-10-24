"""Render the TurnPing icon to PNG/ICO assets (run after changing desktop/theme.py)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImageWriter

from turnping.desktop.theme import draw_logo

ROOT = Path(__file__).resolve().parents[1]
PKG_ASSETS = ROOT / "src" / "turnping" / "desktop" / "assets"
REPO_ASSETS = ROOT / "assets" / "icons"


def main() -> int:
    QGuiApplication([*sys.argv[:1], "-platform", "offscreen"])
    PKG_ASSETS.mkdir(parents=True, exist_ok=True)
    REPO_ASSETS.mkdir(parents=True, exist_ok=True)
    draw_logo(256).save(str(PKG_ASSETS / "turnping.png"))
    for size in (16, 32, 48, 64, 128, 256, 512):
        draw_logo(size).save(str(REPO_ASSETS / f"turnping-{size}.png"))
    if b"ico" not in [bytes(f) for f in QImageWriter.supportedImageFormats()]:
        print("Qt ICO writer unavailable; skipping .ico", file=sys.stderr)
        return 1
    # Qt writes a single-image ICO; Windows scales it. 256px keeps it crisp.
    draw_logo(256).save(str(PKG_ASSETS / "turnping.ico"))
    print(f"wrote icons to {PKG_ASSETS} and {REPO_ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
