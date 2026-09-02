<#
.SYNOPSIS
    Build a distributable EpisodeSleuth package for Windows x64.

.DESCRIPTION
    Produces, in dist\:
        EpisodeSleuth-<version>-windows-x64.zip

    The .zip contains the standalone one-folder binary bundle (no Python
    required) plus a simple Install-EpisodeSleuth.ps1 / .bat that copies the
    app into Program Files (or LocalAppData when not elevated) and creates
    Desktop + Start-menu shortcuts. The recipient can also just unzip and run
    EpisodeSleuth\EpisodeSleuth.exe directly - no install needed.

.PARAMETER Build
    Always rebuild the binary first (runs build_binary.ps1). By default the
    existing dist\EpisodeSleuth is reused if present.

.PARAMETER BundleModel
    Pass through to build_binary.ps1 to pack the Vosk model (fully offline).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\package_windows.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\package_windows.ps1 -Build -BundleModel
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$BundleModel
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

function Info($m) { Write-Host "[*] $m"  -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m"  -ForegroundColor Yellow }

# --- version ----------------------------------------------------------------
$Version = "0.0.0"
$initTxt = Get-Content -Raw ".\__init__.py"
if ($initTxt -match '__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
$Arch    = "x64"
$PkgName = "EpisodeSleuth-$Version-windows-$Arch"
$Bundle  = "dist\EpisodeSleuth"
$ExePath = Join-Path $Bundle "EpisodeSleuth.exe"

Info "Packaging EpisodeSleuth $Version for Windows $Arch"

# --- build if needed --------------------------------------------------------
if ($Build -or -not (Test-Path $ExePath)) {
    Info "Building standalone binary (build_binary.ps1) ..."
    $buildArgs = @()
    if ($BundleModel) { $buildArgs += "-BundleModel" }
    & powershell -ExecutionPolicy Bypass -File ".\build_binary.ps1" @buildArgs
} else {
    Ok "Reusing existing binary at $Bundle"
}

if (-not (Test-Path $ExePath)) {
    throw "Build did not produce $ExePath"
}

# --- stage ------------------------------------------------------------------
$Stage   = Join-Path ([System.IO.Path]::GetTempPath()) ("es_pkg_" + [System.Guid]::NewGuid().ToString("N"))
$PkgRoot = Join-Path $Stage $PkgName
New-Item -ItemType Directory -Force -Path $PkgRoot | Out-Null

Info "Staging bundle ..."
Copy-Item -Recurse -Path $Bundle -Destination (Join-Path $PkgRoot "EpisodeSleuth")

# --- Install-EpisodeSleuth.ps1 (runs on the target machine) -----------------
$installer = @'
<#
  Install EpisodeSleuth (standalone binary) on this machine.
    powershell -ExecutionPolicy Bypass -File .\Install-EpisodeSleuth.ps1
  Run from an elevated prompt to install for all users into Program Files;
  otherwise it installs for the current user into LocalAppData.
    -Uninstall   remove a previous install
#>
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$AppName = "EpisodeSleuth"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (Test-Admin) {
    $InstallDir = Join-Path $env:ProgramFiles $AppName
    $Scope = "all users"
} else {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
    $Scope = "current user"
}
$TargetExe = Join-Path $InstallDir "EpisodeSleuth.exe"
$Desktop   = [Environment]::GetFolderPath("Desktop")
$Programs  = [Environment]::GetFolderPath("Programs")
$DeskLnk   = Join-Path $Desktop "EpisodeSleuth.lnk"
$MenuDir   = Join-Path $Programs "EpisodeSleuth"
$MenuLnk   = Join-Path $MenuDir "EpisodeSleuth.lnk"

if ($Uninstall) {
    Write-Host "[*] Uninstalling EpisodeSleuth ($Scope) ..."
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    if (Test-Path $DeskLnk)    { Remove-Item -Force $DeskLnk }
    if (Test-Path $MenuDir)    { Remove-Item -Recurse -Force $MenuDir }
    Write-Host "[OK] Removed." -ForegroundColor Green
    return
}

Write-Host "[*] Installing EpisodeSleuth ($Scope) to $InstallDir ..."
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Recurse -Force -Path (Join-Path $Here "EpisodeSleuth\*") -Destination $InstallDir

# Icon: prefer the bundled app.ico, else the exe's own icon.
$IconPath = Join-Path $InstallDir "_internal\packaging\app.ico"
if (-not (Test-Path $IconPath)) { $IconPath = $TargetExe }

$wsh = New-Object -ComObject WScript.Shell
function New-Shortcut($linkPath) {
    $dir = Split-Path -Parent $linkPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $sc = $wsh.CreateShortcut($linkPath)
    $sc.TargetPath       = $TargetExe
    $sc.WorkingDirectory = $InstallDir
    $sc.IconLocation     = "$IconPath,0"
    $sc.Description       = "EpisodeSleuth - phonetic dialogue fingerprinting"
    $sc.Save()
}
New-Shortcut $DeskLnk
New-Shortcut $MenuLnk

Write-Host "[OK] Installed." -ForegroundColor Green
Write-Host "     Launch from the Desktop or Start-menu 'EpisodeSleuth' shortcut."
Write-Host "     Reminder: FFmpeg must be installed for audio decoding:"
Write-Host "               winget install Gyan.FFmpeg"
'@
Set-Content -Path (Join-Path $PkgRoot "Install-EpisodeSleuth.ps1") -Value $installer -Encoding UTF8

# --- Install-EpisodeSleuth.bat (double-click convenience) --------------------
$bat = @'
@echo off
REM Double-click to install EpisodeSleuth. Right-click -> "Run as administrator"
REM to install for all users; otherwise it installs for the current user.
powershell -ExecutionPolicy Bypass -File "%~dp0Install-EpisodeSleuth.ps1" %*
pause
'@
Set-Content -Path (Join-Path $PkgRoot "Install-EpisodeSleuth.bat") -Value $bat -Encoding ASCII

# --- README inside the package ----------------------------------------------
$readme = @"
EpisodeSleuth $Version - Windows x64 standalone build
=====================================================

This bundle runs WITHOUT Python installed. It contains a self-contained .exe.

Quick start (no install)
------------------------
  Open the EpisodeSleuth folder and double-click EpisodeSleuth.exe.
  (Windows SmartScreen may warn about an unknown publisher: click
   "More info" -> "Run anyway". This is expected for unsigned apps.)

Install (creates shortcuts)
---------------------------
  Double-click Install-EpisodeSleuth.bat
     - Run normally    -> installs for the current user (LocalAppData)
     - Run as admin    -> installs for all users (Program Files)
  A Desktop and Start-menu "EpisodeSleuth" shortcut are created.

Uninstall
---------
  powershell -ExecutionPolicy Bypass -File .\Install-EpisodeSleuth.ps1 -Uninstall

Requirements
------------
  * FFmpeg must be installed for audio decoding:
        winget install Gyan.FFmpeg
  * The offline speech model is downloaded on first run from the app's
    Settings page, unless this build already bundles it.

The EpisodeSleuth\ folder is self-contained: EpisodeSleuth.exe and its
_internal\ directory must stay together.
"@
Set-Content -Path (Join-Path $PkgRoot "README-INSTALL.txt") -Value $readme -Encoding UTF8

# --- zip --------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path "dist" | Out-Null
$Zip = "dist\$PkgName.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Info "Creating $Zip ..."
# Include the top-level $PkgName folder so the zip extracts into one tidy
# directory instead of scattering files into the current folder.
Compress-Archive -Path $PkgRoot -DestinationPath $Zip -CompressionLevel Optimal

Remove-Item -Recurse -Force $Stage
$size = "{0:N0} MB" -f ((Get-Item $Zip).Length / 1MB)
Ok "Wrote $Zip ($size)"
Ok "Done. Artifact is in dist\."
