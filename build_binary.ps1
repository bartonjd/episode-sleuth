<#
.SYNOPSIS
    Build a standalone EpisodeSleuth .exe on Windows (no MSIX, no Store).

.DESCRIPTION
    Produces a self-contained one-folder bundle that runs the Fluent GUI
    without requiring Python on the target machine:

        dist\EpisodeSleuth\EpisodeSleuth.exe

    This is the simplest way to hand someone a runnable app: zip the
    dist\EpisodeSleuth folder and they double-click EpisodeSleuth.exe.

    For Microsoft Store / signed-installer distribution, use build_msix.ps1
    instead (it reuses the same PyInstaller output under the hood).

    The build runs inside a throwaway .buildvenv so your system Python stays
    clean.

.PARAMETER BundleModel
    Also pack the Vosk speech model from models\ into the bundle, producing a
    fully offline app (much larger). Default: the model is downloaded on first
    run / by install.ps1.

.PARAMETER OneFile
    Build a single EpisodeSleuth.exe instead of a one-folder bundle (slower to
    start, but a single file to share).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build_binary.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build_binary.ps1 -BundleModel
#>
[CmdletBinding()]
param(
    [switch]$BundleModel,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = (Resolve-Path $ProjectDir).Path
Set-Location $ProjectDir

function Info($m) { Write-Host "[*] $m"  -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }

# Surface the switches to the .spec via environment variables.
if ($BundleModel) { $env:BUNDLE_MODEL = "1" } else { $env:BUNDLE_MODEL = "0" }
if ($OneFile)     { $env:ONEFILE      = "1" } else { $env:ONEFILE      = "0" }

# --- pick a Python -----------------------------------------------------------
$python = $null
foreach ($cand in @("python", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        try { if ((& $cand --version 2>&1) -match "Python 3") { $python = $cand; break } } catch { }
    }
}
if (-not $python) { throw "Python 3 not found on PATH. Install it (winget install Python.Python.3.12) and retry." }

# --- throwaway build venv ----------------------------------------------------
$buildVenv = Join-Path $ProjectDir ".buildvenv"
if (-not (Test-Path $buildVenv)) {
    Info "Creating build virtual environment (.buildvenv) ..."
    & $python -m venv $buildVenv
}
$bpy = Join-Path $buildVenv "Scripts\python.exe"

Info "Installing PyInstaller + runtime dependencies ..."
& $bpy -m pip install --upgrade pip | Out-Host
& $bpy -m pip install pyinstaller | Out-Host
& $bpy -m pip install -r (Join-Path $ProjectDir "requirements.txt") | Out-Host
Ok "Build dependencies installed."

Info "Cleaning previous build output ..."
if (Test-Path (Join-Path $ProjectDir "build")) { Remove-Item -Recurse -Force (Join-Path $ProjectDir "build") }
if (Test-Path (Join-Path $ProjectDir "dist"))  { Remove-Item -Recurse -Force (Join-Path $ProjectDir "dist") }

Info "Running PyInstaller (this can take a few minutes) ..."
& $bpy -m PyInstaller (Join-Path $ProjectDir "episodesleuth.spec") --noconfirm | Out-Host

if ($OneFile) {
    $target = Join-Path $ProjectDir "dist\EpisodeSleuth.exe"
} else {
    $target = Join-Path $ProjectDir "dist\EpisodeSleuth\EpisodeSleuth.exe"
}

if (-not (Test-Path $target)) { throw "Build finished but $target was not produced." }

Ok "Build complete."
Write-Host ""
Write-Host "  Run it:   $target"
Write-Host "  Share it: zip the dist\EpisodeSleuth folder (one-folder build) and"
Write-Host "            the recipient double-clicks EpisodeSleuth.exe - no Python needed."
Write-Host ""
