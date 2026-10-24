<#
.SYNOPSIS
  Uninstall AgentPing for the current user.

.DESCRIPTION
  1. Asks the running app to exit.
  2. Removes ONLY AgentPing's hooks from Claude Code, Codex and Gemini CLI settings
     (backups are taken first; other tools' hooks and settings are preserved).
  3. Removes the per-user registrations (agentping:// handler, toast app id, sign-in
     startup entry).
  4. Removes the program files and Start menu shortcut.
  History and preferences in %LOCALAPPDATA%\AgentPing are kept unless -PurgeData is
  given. Backups of agent settings are always preserved (moved next to the data folder
  when purging). Your projects are never touched.

.EXAMPLE
  .\scripts\uninstall.ps1
  .\scripts\uninstall.ps1 -PurgeData
#>
param(
    [switch]$PurgeData,
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\AgentPing"
)

$ErrorActionPreference = "Stop"
$Candidates = @(
    (Join-Path $InstallDir "app\agentping-cli.exe"),
    (Join-Path $InstallDir "venv\Scripts\agentping.exe")
)
$Cli = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($Cli) {
    & $Cli quit | Out-Null
    Start-Sleep -Seconds 2
    $UninstallArgs = @("uninstall")
    if ($PurgeData) { $UninstallArgs += "--purge-data" }
    & $Cli @UninstallArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning "agentping uninstall reported a problem (see above)." }
} else {
    Write-Warning "AgentPing program not found in $InstallDir; skipping hook removal."
    Write-Warning "If you installed from source elsewhere, run: agentping uninstall"
}

$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "AgentPing.lnk"
if (Test-Path $Shortcut) { Remove-Item -Force $Shortcut }
if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
    Write-Host "Removed program files: $InstallDir"
}
Write-Host "AgentPing has been uninstalled."
