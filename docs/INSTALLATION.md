# Installation

TurnPing installs per user. No administrator rights are needed and nothing is written
outside your user profile.

## Option A: installer (recommended)

1. Download `TurnPing-<version>-Setup.exe` (and its `.sha256`) from
   [Releases](https://github.com/premm25/TurnPing/releases), then optionally verify:
   `Get-FileHash .\TurnPing-0.1.0-Setup.exe -Algorithm SHA256`
2. Run it (see [SmartScreen](#smartscreen-and-antivirus)). Choices:
   - **Connect the AI coding agents found on this PC** (on): runs `turnping-cli install`,
     which adds hooks for every detected agent with the safety steps below.
   - **Start TurnPing when I sign in to Windows** (on).
   - **Create a desktop shortcut** (off).
3. Start a new agent session. Hooks are read when a session starts.

It installs to `%LOCALAPPDATA%\Programs\TurnPing\app` (the same place as `install.ps1`, so
either method upgrades the other), adds a Start-menu shortcut and an entry in
**Settings → Apps**. Running a newer Setup upgrades in place: it stops the running app,
replaces the program files and re-checks the hooks.

Silent install: `TurnPing-0.1.0-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /TASKS="connectagents"`
(add `,startup` to the task list to start with Windows).

## Option A2: release zip

1. Download `TurnPing-<version>-win64.zip` and `.sha256`, then verify:
   `Get-FileHash .\TurnPing-0.1.0-win64.zip -Algorithm SHA256`
2. From a checkout of this repository (for the scripts), run:
   `.\scripts\install.ps1 -Zip .\TurnPing-0.1.0-win64.zip [-Startup] [-NoHooks]`

## Option B: build from source

```powershell
.\scripts\build.ps1        # venv in %LOCALAPPDATA%\TurnPing-build, tests, PyInstaller
.\scripts\install.ps1      # installs dist\TurnPing
```

`build.ps1` produces `dist\TurnPing\TurnPing.exe` (tray app), `turnping-cli.exe`
(console CLI used by hooks), `dist\TurnPing-<version>-win64.zip` and, when
[Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed
(`winget install JRSoftware.InnoSetup --scope user`), `dist\TurnPing-<version>-Setup.exe`
built from `packaging\turnping.iss`. Each gets a SHA-256 file.
Build options: `-Python <path>`, `-Venv <dir>`, `-SkipTests`, `-RequireSetup`.

## Option C: development install

```powershell
py -3 -m venv C:\venvs\turnping      # tip: keep venvs outside OneDrive-synced folders
C:\venvs\turnping\Scripts\pip install -e ".[dev]"
C:\venvs\turnping\Scripts\turnping install claude-code
C:\venvs\turnping\Scripts\turnping run
```

Hooks then run `"<venv>\Scripts\python.exe" -I -m turnping hook <provider>`. If you
delete or move the venv, `turnping doctor` reports the hooks as **Misconfigured**.
Re-run `turnping install` to repair them.

## What `install.ps1` does

1. Copies the program to `%LOCALAPPDATA%\Programs\TurnPing` (`app\` or `venv\`).
2. Creates a Start-menu shortcut.
3. Runs `turnping-cli install`, which:
   - installs hooks for every detected agent (see [INTEGRATIONS.md](INTEGRATIONS.md)),
     backing up each settings file to `%LOCALAPPDATA%\TurnPing\backups\<provider>\`;
   - registers the toast identity (`HKCU\Software\Classes\AppUserModelId\TurnPing.Desktop`)
     and the click handler (`HKCU\Software\Classes\turnping`);
   - with `-Startup`, adds `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\TurnPing`.
4. Runs `turnping doctor` and starts TurnPing in the tray (skip with `-NoLaunch`).

Re-running is safe: hooks aren't duplicated, and an unchanged settings file isn't rewritten.

### Safety of agent settings edits

- The file is parsed first. If it isn't plain JSON (comments, trailing commas) or has an
  unexpected shape, TurnPing **refuses to modify it** and prints manual instructions.
- A timestamped backup is taken before any change (the last 20 are kept).
- Only TurnPing's handlers are added, identified by their command
  (`... turnping ... hook <provider>`). Every other key, hook group and handler is
  preserved in order.
- The write is atomic and aborted if another program changed the file meanwhile.
- The result is re-read and verified. On failure the backup is restored.

## Start with Windows

Opt in during install (`-Startup`), in Settings → General, or with
`turnping install --no-hooks --startup`. Disable it in Settings, or remove the `TurnPing`
value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Uninstall removes it.

## Windows notification permission

TurnPing uses native toasts when Windows allows them. If **Settings → System →
Notifications** is off (globally or for TurnPing), TurnPing shows its own popups while
the app is running and the dashboard explains why. Focus / Do Not Disturb can hide
toasts; TurnPing then reports them as *dispatched* rather than *confirmed*.

## SmartScreen and antivirus

Release builds are **not code-signed** (no certificate is available). On first run,
Windows SmartScreen may show "Windows protected your PC": choose *More info → Run anyway*
only if you verified the SHA-256 checksum or built it yourself. Some antivirus products
flag PyInstaller executables heuristically. Building from source avoids trusting a
binary. Freshly built files can be briefly locked by scanners or sync clients;
`build.ps1` retries archiving for that reason.

## Uninstall

Installed with Setup: **Settings → Apps → Installed apps → TurnPing → Uninstall**. It runs
`turnping-cli uninstall` (the same steps as below) and asks whether to delete history and
preferences (default: keep).

Installed with the scripts:

```powershell
.\scripts\uninstall.ps1             # keeps history/preferences
.\scripts\uninstall.ps1 -PurgeData  # deletes %LOCALAPPDATA%\TurnPing too
```

This asks the app to exit, removes only TurnPing's hook entries (with backups), removes
the registry entries and the shortcut, and deletes the program folder. With
`-PurgeData`, backups of agent settings are first copied to
`%LOCALAPPDATA%\TurnPing-settings-backups`. For a dev install, run `turnping uninstall
[--purge-data]` instead.
