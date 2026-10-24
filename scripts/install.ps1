<#
.SYNOPSIS
  Install AgentPing for the current user (no administrator rights needed).

.DESCRIPTION
  Installs to %LOCALAPPDATA%\Programs\AgentPing from one of:
    -Zip <path>     a release archive (AgentPing-<version>-win64.zip)
    -Dist <path>    a built folder (default: .\dist\AgentPing if it exists)
    -FromSource     a virtual environment with AgentPing installed from this repository

  Then it:
    * creates a Start menu shortcut,
    * installs hooks for detected coding agents (skip with -NoHooks; existing agent
      settings are backed up and preserved; see docs\INSTALLATION.md),
    * optionally registers start-at-sign-in (-Startup),
    * starts AgentPing in the tray.

  Re-running is safe: files are replaced, hooks are not duplicated.

.EXAMPLE
  .\scripts\install.ps1
  .\scripts\install.ps1 -Zip .\AgentPing-0.1.0-win64.zip -Startup
  .\scripts\install.ps1 -FromSource -NoHooks
#>
param(
    [string]$Zip = "",
    [string]$Dist = "",
    [switch]$FromSource,
    [switch]$NoHooks,
    [switch]$Startup,
    [switch]$NoLaunch,
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\AgentPing"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Stop-RunningAgentPing([string]$Cli) {
    if (Test-Path $Cli) {
        & $Cli quit | Out-Null
        Start-Sleep -Seconds 2
    }
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($FromSource) {
    $Venv = Join-Path $InstallDir "venv"
    $Cli = Join-Path $Venv "Scripts\agentping.exe"
    Stop-RunningAgentPing $Cli
    if (-not (Test-Path "$Venv\Scripts\python.exe")) {
        if (Get-Command py -ErrorAction SilentlyContinue) { Invoke-Checked "py" @("-3", "-m", "venv", $Venv) }
        else { Invoke-Checked "python" @("-m", "venv", $Venv) }
    }
    Invoke-Checked "$Venv\Scripts\python.exe" @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
    Invoke-Checked "$Venv\Scripts\python.exe" @("-m", "pip", "install", "--quiet", $Root)
    $Gui = Join-Path $Venv "Scripts\agentping-app.exe"
} else {
    $Target = Join-Path $InstallDir "app"
    $Cli = Join-Path $Target "agentping-cli.exe"
    Stop-RunningAgentPing $Cli
    if ($Zip) {
        $Staging = Join-Path $env:TEMP ("agentping-install-" + [guid]::NewGuid())
        Expand-Archive -Path $Zip -DestinationPath $Staging
        $Source = Join-Path $Staging "AgentPing"
    } else {
        if (-not $Dist) { $Dist = Join-Path $Root "dist\AgentPing" }
        if (-not (Test-Path (Join-Path $Dist "AgentPing.exe"))) {
            throw "No build found at $Dist. Run .\scripts\build.ps1, pass -Zip, or use -FromSource."
        }
        $Source = $Dist
    }
    if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
    Copy-Item -Recurse -Path $Source -Destination $Target
    if ($Zip) { Remove-Item -Recurse -Force $Staging }
    $Gui = Join-Path $Target "AgentPing.exe"
}

# Start menu shortcut (also lets you find AgentPing in Windows search).
$Programs = [Environment]::GetFolderPath("Programs")
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut((Join-Path $Programs "AgentPing.lnk"))
$Link.TargetPath = $Gui
$Link.Arguments = "run"
$Link.Description = "AgentPing - desktop notifications for AI coding agents"
$Link.Save()

Write-Host "Installed AgentPing to $InstallDir"
Invoke-Checked $Cli @("--version")

$InstallArgs = @("install")
if ($NoHooks) { $InstallArgs += "--no-hooks" }  # only the click handler / toast identity
if ($Startup) { $InstallArgs += "--startup" }
Invoke-Checked $Cli $InstallArgs
if ($NoHooks) { Write-Host "Agent hooks skipped. Later: `"$Cli`" install claude-code" }

& $Cli doctor  # informational; problems are reported, not fatal
if (-not $NoLaunch) {
    Start-Process -FilePath $Gui -ArgumentList @("run", "--background")
    Write-Host "AgentPing is running in the tray. Test it with: `"$Cli`" test-notification"
}
