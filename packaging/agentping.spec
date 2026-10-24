# PyInstaller spec: one folder, two executables sharing the same runtime files.
#   AgentPing.exe      windowed desktop app (tray, dashboard, popups)
#   agentping-cli.exe  console CLI used by agent hooks (needs stdin/stdout)
# Build with scripts/build.ps1.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "src" / "agentping" / "desktop" / "assets" / "agentping.ico")
datas = [(str(ROOT / "src" / "agentping" / "desktop" / "assets"), "agentping/desktop/assets")]
hidden = collect_submodules("agentping") + collect_submodules("winrt")
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore"]


def analysis(script):
    return Analysis(
        [str(ROOT / "packaging" / script)],
        pathex=[str(ROOT / "src")],
        datas=datas,
        hiddenimports=hidden,
        excludes=excludes,
        noarchive=False,
    )


gui = analysis("gui_entry.py")
cli = analysis("cli_entry.py")

gui_exe = EXE(
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="AgentPing",
    console=False, icon=ICON, version=str(ROOT / "packaging" / "version_info.txt"),
)
cli_exe = EXE(
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="agentping-cli",
    console=True, icon=ICON, version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    name="AgentPing",
)
