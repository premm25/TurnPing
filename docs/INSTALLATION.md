# Installation

AgentPing installs per user. No administrator rights are needed and nothing is written
outside your user profile.

## Option A: release zip

1. Download `AgentPing-<version>-win64.zip` and `.sha256`, then verify:
   `Get-FileHash .\AgentPing-0.1.0-win64.zip -Algorithm SHA256`
2. From a checkout of this repository (for the scripts), run:
   `.\scripts\install.ps1 -Zip .\AgentPing-0.1.0-win64.zip [-Startup] [-NoHooks]`

## Option B: build from source

```powershell
.\scripts\build.ps1        # venv in %LOCALAPPDATA%\AgentPing-build, tests, PyInstaller
.\scripts\install.ps1      # installs dist\AgentPing
```

`build.ps1` produces `dist\AgentPing\AgentPing.exe` (tray app), `agentping-cli.exe`
(console CLI used by hooks) and `dist\AgentPing-<version>-win64.zip` with a SHA-256 file.
Build options: `-Python <path>`, `-Venv <dir>`, `-SkipTests`.

## Option C: development install

```powershell
py -3 -m venv C:\venvs\agentping      # tip: keep venvs outside OneDrive-synced folders
C:\venvs\agentping\Scripts\pip install -e ".[dev]"
C:\venvs\agentping\Scripts\agentping install claude-code
C:\venvs\agentping\Scripts\agentping run
```

Hooks then run `"<venv>\Scripts\python.exe" -I -m agentping hook <provider>`. If you
delete or move the venv, `agentping doctor` reports the hooks as **Misconfigured**.
Re-run `agentping install` to repair them.

## What `install.ps1` does

1. Copies the program to `%LOCALAPPDATA%\Programs\AgentPing` (`app\` or `venv\`).
2. Creates a Start-menu shortcut.
3. Runs `agentping-cli install`, which:
   - installs hooks for every detected agent (see [INTEGRATIONS.md](INTEGRATIONS.md)),
     backing up each settings file to `%LOCALAPPDATA%\AgentPing\backups\<provider>\`;
   - registers the toast identity (`HKCU\Software\Classes\AppUserModelId\AgentPing.Desktop`)
     and the click handler (`HKCU\Software\Classes\agentping`);
   - with `-Startup`, adds `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\AgentPing`.
4. Runs `agentping doctor` and starts AgentPing in the tray (skip with `-NoLaunch`).

Re-running is safe: hooks aren't duplicated, and an unchanged settings file isn't rewritten.

### Safety of agent settings edits

- The file is parsed first. If it isn't plain JSON (comments, trailing commas) or has an
  unexpected shape, AgentPing **refuses to modify it** and prints manual instructions.
- A timestamped backup is taken before any change (the last 20 are kept).
- Only AgentPing's handlers are added, identified by their command
  (`... agentping ... hook <provider>`). Every other key, hook group and handler is
  preserved in order.
- The write is atomic and aborted if another program changed the file meanwhile.
- The result is re-read and verified. On failure the backup is restored.

## Start with Windows

Opt in during install (`-Startup`), in Settings → General, or with
`agentping install --no-hooks --startup`. Disable it in Settings, or remove the `AgentPing`
value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Uninstall removes it.

## Windows notification permission

AgentPing uses native toasts when Windows allows them. If **Settings → System →
Notifications** is off (globally or for AgentPing), AgentPing shows its own popups while
the app is running and the dashboard explains why. Focus / Do Not Disturb can hide
toasts; AgentPing then reports them as *dispatched* rather than *confirmed*.

## SmartScreen and antivirus

Release builds are **not code-signed** (no certificate is available). On first run,
Windows SmartScreen may show "Windows protected your PC": choose *More info → Run anyway*
only if you verified the SHA-256 checksum or built it yourself. Some antivirus products
flag PyInstaller executables heuristically. Building from source avoids trusting a
binary. Freshly built files can be briefly locked by scanners or sync clients;
`build.ps1` retries archiving for that reason.

## Uninstall

```powershell
.\scripts\uninstall.ps1             # keeps history/preferences
.\scripts\uninstall.ps1 -PurgeData  # deletes %LOCALAPPDATA%\AgentPing too
```

This asks the app to exit, removes only AgentPing's hook entries (with backups), removes
the registry entries and the shortcut, and deletes the program folder. With
`-PurgeData`, backups of agent settings are first copied to
`%LOCALAPPDATA%\AgentPing-settings-backups`. For a dev install, run `agentping uninstall
[--purge-data]` instead.
