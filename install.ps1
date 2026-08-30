<#
.SYNOPSIS
    One-shot Windows setup for the DVD Episode Identifier.

.DESCRIPTION
    Installs everything the app needs and creates a Start-menu / Desktop
    shortcut so you can launch the GUI without touching the command line:

      1. Python 3            (via winget, if not already present)
      2. FFmpeg              (via winget - needed to decode video/audio)
      3. fpcalc / Chromaprint(via winget: AcoustID.Chromaprint - acoustic engine)
      4. A local virtual env (.venv) with the Python packages from requirements.txt
         (this includes PySide6-Fluent-Widgets, the modern GUI toolkit)
      5. The Vosk offline speech model (downloaded + unzipped into models\)
      6. Shortcuts that launch dvd_identifier_fluent.py with the venv's Python

    Re-running is safe: anything already installed is detected and skipped.

.PARAMETER NoVenv
    Install the Python packages into the current Python instead of a .venv.

.PARAMETER NoModel
    Skip downloading the Vosk speech model (acoustic-only users don't need it).

.PARAMETER NoShortcut
    Do not create Desktop / Start-menu shortcuts.

.PARAMETER Force
    Reinstall Python packages and re-download the model even if present.

.EXAMPLE
    # Right-click -> Run with PowerShell, or:
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoVenv,
    [switch]$NoModel,
    [switch]$NoShortcut,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

$VoskModelName = "vosk-model-small-en-us-0.15"
$VoskUrl       = "https://alphacephei.com/vosk/models/$VoskModelName.zip"
$ModelDir      = Join-Path $ProjectDir "models\$VoskModelName"

function Info($m)  { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "[!] $m" -ForegroundColor Yellow }
function Fail($m)  { Write-Host "[X] $m" -ForegroundColor Red }

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Test-Winget {
    if (-not (Test-Command "winget")) {
        Fail "winget is not available on this system."
        Write-Host "  winget ships with 'App Installer' from the Microsoft Store."
        Write-Host "  Install it, then re-run this script. (Windows 10 1809+ / Windows 11)"
        throw "winget missing"
    }
}

function Install-WingetPackage($id, $friendly) {
    Info "Installing $friendly ($id) via winget ..."
    # --accept-* keeps it non-interactive; exit code is non-zero if already installed
    winget install --id $id -e --source winget `
        --accept-package-agreements --accept-source-agreements 2>&1 |
        Out-Host
    Ok "$friendly step finished."
}

Write-Host "======================================================================"
Write-Host "  DVD Episode Identifier - Windows setup"
Write-Host "======================================================================"
Write-Host "  Project folder: $ProjectDir"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
$python = $null
foreach ($cand in @("python", "py")) {
    if (Test-Command $cand) {
        try {
            $v = & $cand --version 2>&1
            if ($v -match "Python 3") { $python = $cand; break }
        } catch { }
    }
}
if (-not $python) {
    Test-Winget
    Install-WingetPackage "Python.Python.3.12" "Python 3.12"
    Warn "Python was just installed. If the next step fails with 'python not found',"
    Warn "close this window and run install.ps1 again in a NEW terminal (PATH refresh)."
    # Try to pick it up in the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    foreach ($cand in @("python", "py")) {
        if (Test-Command $cand) { $python = $cand; break }
    }
    if (-not $python) { throw "Python still not on PATH - re-run in a new terminal." }
}
Ok "Using Python: $(& $python --version 2>&1)"

# ---------------------------------------------------------------------------
# 2. FFmpeg
# ---------------------------------------------------------------------------
if (Test-Command "ffmpeg") {
    Ok "FFmpeg already installed."
} else {
    Test-Winget
    Install-WingetPackage "Gyan.FFmpeg" "FFmpeg"
}

# ---------------------------------------------------------------------------
# 3. fpcalc (Chromaprint)
# ---------------------------------------------------------------------------
if (Test-Command "fpcalc") {
    Ok "fpcalc already installed."
} else {
    Test-Winget
    # This is exactly the package the user confirmed:  winget search fpcalc
    Install-WingetPackage "AcoustID.Chromaprint" "Chromaprint (fpcalc)"
}

# refresh PATH so freshly-installed tools are visible to later checks
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

# ---------------------------------------------------------------------------
# 4. Python virtual environment + packages
# ---------------------------------------------------------------------------
$venvPy = $null
if ($NoVenv) {
    Info "Installing Python packages into the current Python (--NoVenv)."
    & $python -m pip install --upgrade pip | Out-Host
    & $python -m pip install -r (Join-Path $ProjectDir "requirements.txt") | Out-Host
    $venvPy  = (Get-Command $python).Source
    $venvPyw = $venvPy   # no separate pythonw guaranteed; console launcher will be used
} else {
    $venvDir = Join-Path $ProjectDir ".venv"
    if ((Test-Path $venvDir) -and $Force) {
        Warn "Removing existing .venv (--Force) ..."
        Remove-Item -Recurse -Force $venvDir
    }
    if (-not (Test-Path $venvDir)) {
        Info "Creating virtual environment in .venv ..."
        & $python -m venv $venvDir
    } else {
        Ok "Virtual environment .venv already exists."
    }
    $venvPy  = Join-Path $venvDir "Scripts\python.exe"
    $venvPyw = Join-Path $venvDir "Scripts\pythonw.exe"
    Info "Installing/upgrading Python packages ..."
    & $venvPy -m pip install --upgrade pip | Out-Host
    & $venvPy -m pip install -r (Join-Path $ProjectDir "requirements.txt") | Out-Host
}
Ok "Python packages installed."

# ---------------------------------------------------------------------------
# 5. Vosk speech model (offline STT)
# ---------------------------------------------------------------------------
if ($NoModel) {
    Warn "Skipping Vosk model download (--NoModel). Phonetic matching will not work"
    Warn "until a model exists at models\$VoskModelName."
} elseif ((Test-Path $ModelDir) -and -not $Force) {
    Ok "Vosk model already present ($VoskModelName)."
} else {
    Info "Downloading Vosk model ($VoskModelName, ~40 MB) ..."
    $zip = Join-Path $env:TEMP "$VoskModelName.zip"
    Invoke-WebRequest -Uri $VoskUrl -OutFile $zip
    $modelsRoot = Join-Path $ProjectDir "models"
    New-Item -ItemType Directory -Force -Path $modelsRoot | Out-Null
    if ((Test-Path $ModelDir) -and $Force) { Remove-Item -Recurse -Force $ModelDir }
    Info "Extracting model ..."
    Expand-Archive -Path $zip -DestinationPath $modelsRoot -Force
    Remove-Item $zip -Force
    if (Test-Path $ModelDir) { Ok "Vosk model ready." }
    else { Warn "Model extracted but folder name differs - check models\ folder." }
}

# ---------------------------------------------------------------------------
# 6. Launcher + shortcuts
# ---------------------------------------------------------------------------
# Write a launcher that uses the venv's pythonw (no console window).
$launcher = Join-Path $ProjectDir "Launch_DVD_Identifier.bat"
$pywForBat = if (Test-Path $venvPyw) { $venvPyw } else { "pythonw" }
$pyForBat  = if (Test-Path $venvPy)  { $venvPy }  else { "python" }
@"
@echo off
REM Auto-generated by install.ps1 - launches the GUI with the project's Python.
cd /d "%~dp0"
start "" "$pywForBat" "dvd_identifier_fluent.py"
"@ | Set-Content -Encoding ASCII $launcher
Ok "Wrote launcher: Launch_DVD_Identifier.bat"

if (-not $NoShortcut) {
    $icon = Join-Path $ProjectDir "packaging\app.ico"
    $wsh  = New-Object -ComObject WScript.Shell

    function New-Shortcut($linkPath) {
        $sc = $wsh.CreateShortcut($linkPath)
        # Prefer the windowless pythonw directly so the shortcut has a clean target
        if (Test-Path $venvPyw) {
            $sc.TargetPath = $venvPyw
            $sc.Arguments  = '"' + (Join-Path $ProjectDir "dvd_identifier_fluent.py") + '"'
        } else {
            $sc.TargetPath = $launcher
        }
        $sc.WorkingDirectory = $ProjectDir
        if (Test-Path $icon) { $sc.IconLocation = $icon }
        $sc.Description = "Identify DVD-ripped episodes for Plex"
        $sc.Save()
    }

    $desktop = [Environment]::GetFolderPath("Desktop")
    New-Shortcut (Join-Path $desktop "DVD Episode Identifier.lnk")
    Ok "Created Desktop shortcut."

    $startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "DVD Episode Identifier"
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
    New-Shortcut (Join-Path $startMenu "DVD Episode Identifier.lnk")
    Ok "Created Start-menu shortcut."
}

# ---------------------------------------------------------------------------
# Summary + quick verification
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================================"
Write-Host "  Setup complete"
Write-Host "======================================================================"
foreach ($t in @("ffmpeg", "fpcalc")) {
    if (Test-Command $t) { Ok "$t on PATH" } else { Warn "$t NOT on PATH yet - open a new terminal" }
}
Write-Host ""
Write-Host "Launch the app any time by:"
Write-Host "  - double-clicking the 'DVD Episode Identifier' Desktop shortcut, or"
Write-Host "  - double-clicking Launch_DVD_Identifier.bat, or"
Write-Host "  - running:  $pyForBat dvd_identifier_fluent.py"
Write-Host ""
Write-Host "If ffmpeg/fpcalc show as 'NOT on PATH', just close and reopen your terminal."
