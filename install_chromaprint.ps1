<#
.SYNOPSIS
    Automatic installer for Chromaprint's fpcalc.exe on Windows.

.DESCRIPTION
    Downloads the latest Chromaprint Windows binary from the official AcoustID /
    GitHub releases, extracts fpcalc.exe, and lets you either copy it into this
    project folder or add it to your user PATH. Finally verifies the install by
    running `fpcalc -version`.

.USAGE
    Open PowerShell in this folder and run:

        powershell -ExecutionPolicy Bypass -File .\install_chromaprint.ps1

    Optional switches:
        -Mode Project   Copy fpcalc.exe into this project folder (no prompt)
        -Mode Path      Install to %LOCALAPPDATA%\Chromaprint and add to PATH
        -Force          Re-download/reinstall even if fpcalc is already found
#>

[CmdletBinding()]
param(
    [ValidateSet("Project", "Path", "Ask")]
    [string]$Mode = "Ask",
    [switch]$Force
)

# Stop on any uncaught error so problems are obvious.
$ErrorActionPreference = "Stop"

# ---------- pretty printing helpers ----------
function Write-Step($msg)    { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "[ OK ] $msg" -ForegroundColor Green }
function Write-WarnMsg($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-ErrMsg($msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red }

# Fallback version if we cannot query GitHub for the very latest.
$FallbackVersion = "1.6.0"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "   Chromaprint (fpcalc) installer for Windows" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta

# ---------- 0. Already installed? ----------
function Test-Fpcalc($exePath) {
    try {
        $out = & $exePath -version 2>&1
        if ($LASTEXITCODE -eq 0 -or $out -match "fpcalc") { return $out }
    } catch { }
    return $null
}

if (-not $Force) {
    Write-Step "Checking whether fpcalc is already available..."
    $existing = Get-Command fpcalc -ErrorAction SilentlyContinue
    if ($existing) {
        $ver = Test-Fpcalc $existing.Source
        if ($ver) {
            Write-Ok "fpcalc is already installed at: $($existing.Source)"
            Write-Host "       $ver"
            Write-Host "`nNothing to do. Re-run with -Force to reinstall." -ForegroundColor Gray
            exit 0
        }
    }
    $localCopy = Join-Path $ProjectDir "fpcalc.exe"
    if (Test-Path $localCopy) {
        $ver = Test-Fpcalc $localCopy
        if ($ver) {
            Write-Ok "fpcalc.exe already present in the project folder."
            Write-Host "       $ver"
            Write-Host "`nNothing to do. Re-run with -Force to reinstall." -ForegroundColor Gray
            exit 0
        }
    }
    Write-Host "       Not found yet - continuing with installation." -ForegroundColor Gray
}

# ---------- 1. Work out the download URL ----------
Write-Step "Determining the latest Chromaprint version..."

# Prefer TLS 1.2+ for older Windows/PowerShell.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$arch = if ([Environment]::Is64BitOperatingSystem) { "x86_64" } else { "i686" }
$downloadUrl = $null
$version = $FallbackVersion

try {
    $api = "https://api.github.com/repos/acoustid/chromaprint/releases/latest"
    $headers = @{ "User-Agent" = "chromaprint-installer" }
    $rel = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 20
    if ($rel.tag_name) { $version = $rel.tag_name.TrimStart("v") }
    $asset = $rel.assets | Where-Object { $_.name -like "*windows-$arch*.zip" } | Select-Object -First 1
    if ($asset) {
        $downloadUrl = $asset.browser_download_url
        Write-Ok "Latest version is $version"
    }
} catch {
    Write-WarnMsg "Could not query GitHub for the latest release ($($_.Exception.Message))."
    Write-WarnMsg "Falling back to version $FallbackVersion."
}

if (-not $downloadUrl) {
    $version = $FallbackVersion
    $downloadUrl = "https://github.com/acoustid/chromaprint/releases/download/v$version/chromaprint-fpcalc-$version-windows-$arch.zip"
}

Write-Host "       Architecture : $arch"
Write-Host "       Download URL : $downloadUrl"

# ---------- 2. Download ----------
Write-Step "Downloading Chromaprint..."
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("chromaprint_" + [Guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$zipPath = Join-Path $tmpDir "chromaprint.zip"

try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 120
    Write-Ok "Downloaded to temporary file."
} catch {
    Write-ErrMsg "Download failed: $($_.Exception.Message)"
    Write-Host  "Please download manually from https://acoustid.org/chromaprint" -ForegroundColor Yellow
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}

# ---------- 3. Extract ----------
Write-Step "Extracting fpcalc.exe..."
$extractDir = Join-Path $tmpDir "extracted"
try {
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
} catch {
    Write-ErrMsg "Extraction failed: $($_.Exception.Message)"
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}

$fpcalc = Get-ChildItem -Path $extractDir -Recurse -Filter "fpcalc.exe" | Select-Object -First 1
if (-not $fpcalc) {
    Write-ErrMsg "Could not find fpcalc.exe inside the downloaded archive."
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Ok "Found fpcalc.exe"

# ---------- 4. Choose install location ----------
if ($Mode -eq "Ask") {
    Write-Host "`nWhere would you like to install fpcalc.exe?" -ForegroundColor Cyan
    Write-Host "  [1] Copy it into THIS project folder (simplest)"
    Write-Host "      $ProjectDir"
    Write-Host "  [2] Install to your user folder and add it to PATH (works everywhere)"
    $choice = Read-Host "Enter 1 or 2"
    switch ($choice) {
        "1" { $Mode = "Project" }
        "2" { $Mode = "Path" }
        default {
            Write-WarnMsg "No valid choice made; defaulting to the project folder."
            $Mode = "Project"
        }
    }
}

$installedPath = $null

if ($Mode -eq "Project") {
    Write-Step "Copying fpcalc.exe into the project folder..."
    $dest = Join-Path $ProjectDir "fpcalc.exe"
    Copy-Item -Path $fpcalc.FullName -Destination $dest -Force
    $installedPath = $dest
    Write-Ok "Copied to $dest"
}
elseif ($Mode -eq "Path") {
    Write-Step "Installing to your user folder and updating PATH..."
    $installDir = Join-Path $env:LOCALAPPDATA "Chromaprint"
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    $dest = Join-Path $installDir "fpcalc.exe"
    Copy-Item -Path $fpcalc.FullName -Destination $dest -Force
    $installedPath = $dest
    Write-Ok "Copied to $dest"

    # Add to the USER PATH (does not require admin rights).
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    $already = ($userPath -split ";") -contains $installDir
    if ($already) {
        Write-Ok "PATH already contains $installDir"
    } else {
        $newPath = if ([string]::IsNullOrEmpty($userPath)) { $installDir } else { "$userPath;$installDir" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        # Update the current session too, so verification below works immediately.
        $env:Path = "$env:Path;$installDir"
        Write-Ok "Added $installDir to your user PATH."
        Write-WarnMsg "Open a NEW terminal later for the PATH change to apply everywhere."
    }
}

# ---------- 5. Verify ----------
Write-Step "Verifying installation (fpcalc -version)..."
$verOut = Test-Fpcalc $installedPath
if ($verOut) {
    Write-Ok "Success!  $verOut"
} else {
    Write-ErrMsg "fpcalc.exe was installed but did not run correctly."
    Write-Host  "Try opening a new terminal and running: fpcalc -version" -ForegroundColor Yellow
}

# ---------- 6. Cleanup ----------
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n============================================================" -ForegroundColor Magenta
Write-Host "   Done." -ForegroundColor Magenta
Write-Host "   fpcalc.exe location: $installedPath" -ForegroundColor Magenta
if ($Mode -eq "Project") {
    Write-Host "   Run the project tools from this folder and they will find it." -ForegroundColor Gray
} else {
    Write-Host "   Remember to open a NEW terminal so PATH changes take effect." -ForegroundColor Gray
}
Write-Host "============================================================" -ForegroundColor Magenta
