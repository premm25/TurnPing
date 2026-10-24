<#
.SYNOPSIS
  Build the TurnPing Windows distribution (dist\TurnPing\) and a zip archive.

.DESCRIPTION
  Creates/uses a virtual environment, installs pinned build dependencies, runs the
  tests (unless -SkipTests), then builds with PyInstaller using packaging\turnping.spec.
  The PyInstaller work directory is kept outside the repository (it can be large and
  this repo may live in a synced folder such as OneDrive).

  Output:
    dist\TurnPing\TurnPing.exe       windowed desktop app
    dist\TurnPing\turnping-cli.exe   console CLI used by agent hooks
    dist\TurnPing-<version>-win64.zip

  The executables are NOT code-signed. See docs\INSTALLATION.md (SmartScreen).

.EXAMPLE
  .\scripts\build.ps1
  .\scripts\build.ps1 -Python C:\Python314\python.exe -SkipTests
#>
param(
    [string]$Python = "",
    [string]$Venv = "$env:LOCALAPPDATA\TurnPing-build\venv",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    if (-not $Python) {
        if (Get-Command py -ErrorAction SilentlyContinue) { $Python = "py" } else { $Python = "python" }
    }
    Write-Host "Creating build environment in $Venv"
    if ($Python -eq "py") { Invoke-Checked "py" @("-3", "-m", "venv", $Venv) }
    else { Invoke-Checked $Python @("-m", "venv", $Venv) }
}
$Py = "$Venv\Scripts\python.exe"
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "-e", ".[dev]")

if (-not $SkipTests) {
    Write-Host "Running tests"
    Invoke-Checked $Py @("-m", "pytest", "-q", "-p", "no:cacheprovider")
}

$Version = (& $Py -c "import turnping; print(turnping.__version__)").Trim()
$Work = "$env:LOCALAPPDATA\TurnPing-build\work"
Write-Host "Building TurnPing $Version"
Invoke-Checked $Py @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", "dist",
    "--workpath", $Work, "packaging\turnping.spec")

$Out = Join-Path $Root "dist\TurnPing"
foreach ($exe in @("TurnPing.exe", "turnping-cli.exe")) {
    if (-not (Test-Path (Join-Path $Out $exe))) { throw "missing build output: $exe" }
}
Write-Host "Smoke test: turnping-cli --version"
Invoke-Checked (Join-Path $Out "turnping-cli.exe") @("--version")

$Zip = Join-Path $Root "dist\TurnPing-$Version-win64.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
# Antivirus scanners and sync clients (e.g. OneDrive) briefly lock freshly built files.
for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
        Compress-Archive -Path $Out -DestinationPath $Zip -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq 6) { throw }
        Write-Host "Archive attempt $attempt failed (file in use); retrying..."
        if (Test-Path $Zip) { Remove-Item $Zip -Force }
        Start-Sleep -Seconds 5
    }
}
$Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash
Set-Content -Path "$Zip.sha256" -Value "$Hash  $(Split-Path -Leaf $Zip)" -Encoding ascii
Write-Host "Built $Out"
Write-Host "Archive $Zip (SHA256 $Hash)"
