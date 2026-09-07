<#
.SYNOPSIS
    Remove build, packaging and Python cache artifacts (Windows).

.DESCRIPTION
    Deletes everything the build/package scripts generate, returning the tree
    to a pristine source state. Source files, docs, the fingerprint database
    and downloaded Vosk models are left untouched.

.PARAMETER Deep
    Also remove the throwaway build venv (.buildvenv).

.PARAMETER DryRun
    Show what would be removed without deleting anything.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\clean.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\clean.ps1 -Deep
#>
param(
    [switch]$Deep,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

function Info($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }

function Remove-Target($target) {
    if (Test-Path -LiteralPath $target) {
        if ($DryRun) {
            Write-Host "  would remove: $target"
        } else {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "  removed: $target"
        }
    }
}

Info "Cleaning build and packaging artifacts ..."
Remove-Target "build"
Remove-Target "dist"
Get-ChildItem -Path . -Filter *.msix -File -ErrorAction SilentlyContinue | ForEach-Object { Remove-Target $_.FullName }
Get-ChildItem -Path . -Filter *.pfx  -File -ErrorAction SilentlyContinue | ForEach-Object { Remove-Target $_.FullName }
Get-ChildItem -Path . -Filter *.cer  -File -ErrorAction SilentlyContinue | ForEach-Object { Remove-Target $_.FullName }
Remove-Target "Launch_DVD_Identifier.bat"
Remove-Target "Launch_EpisodeSleuth.bat"

Info "Cleaning Python packaging metadata ..."
Get-ChildItem -Path . -Filter *.egg-info -Directory -ErrorAction SilentlyContinue | ForEach-Object { Remove-Target $_.FullName }
Remove-Target ".eggs"

Info "Cleaning auto-generated PyInstaller specs (keeping episodesleuth.spec) ..."
Get-ChildItem -Path . -Filter *.spec -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne 'episodesleuth.spec' } |
    ForEach-Object { Remove-Target $_.FullName }

Info "Cleaning Python caches ..."
Get-ChildItem -Path . -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\.buildvenv\*" } |
    ForEach-Object { Remove-Target $_.FullName }
Get-ChildItem -Path . -Recurse -File -Include *.pyc, *.pyo -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\.buildvenv\*" } |
    ForEach-Object { Remove-Target $_.FullName }
Remove-Target ".pytest_cache"
Remove-Target ".coverage"

if ($Deep) {
    Info "Deep clean: removing throwaway build venv ..."
    Remove-Target ".buildvenv"
}

Ok "Clean complete."
