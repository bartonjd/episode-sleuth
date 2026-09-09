<#
.SYNOPSIS
    EpisodeSleuth - unified build front-door (Windows).

.DESCRIPTION
    A thin, self-documenting wrapper over the project's build/package scripts so
    every common task has one obvious command, mirroring the Makefile used on
    Linux/macOS.

.PARAMETER Target
    The task to run. One of:
        help        show this help (default)
        install     install the package and runtime dependencies (editable)
        dev         install with development/test extras
        test        run the test suite
        lint        static-check the codebase with ruff
        lint-fix    auto-fix the lint issues ruff can fix safely
        compile     byte-compile every module to catch syntax errors
        bump-version set the project version ($env:VERSION='x.y.z' first)
        clean       remove build, packaging and Python cache artifacts
        clean-deep  clean and also remove the throwaway build venv
        build       build a standalone one-folder binary (dist\EpisodeSleuth\)
        package     build the Windows release zip
        msix        build the signed MSIX installer (Microsoft Store)
        dist        clean, then build and package from scratch

.EXAMPLE
    pwsh make.ps1 build

.EXAMPLE
    pwsh make.ps1 test
#>
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$Python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }

function Show-Help {
    Write-Host "EpisodeSleuth build commands (pwsh make.ps1 <target>):" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  help        show this help (default)"
    Write-Host "  install     install the package and runtime dependencies (editable)"
    Write-Host "  dev         install with development/test extras"
    Write-Host "  test        run the test suite"
    Write-Host "  lint        static-check the codebase with ruff"
    Write-Host "  lint-fix    auto-fix the lint issues ruff can fix safely"
    Write-Host "  compile     byte-compile every module to catch syntax errors"
    Write-Host "  bump-version set the project version (`$env:VERSION='x.y.z' first)"
    Write-Host "  clean       remove build, packaging and Python cache artifacts"
    Write-Host "  clean-deep  clean and also remove the throwaway build venv"
    Write-Host "  build       build a standalone one-folder binary (dist\EpisodeSleuth\)"
    Write-Host "  package     build the Windows release zip"
    Write-Host "  msix        build the signed MSIX installer (Microsoft Store)"
    Write-Host "  dist        clean, then build and package from scratch"
}

function Invoke-Ps1($script) {
    powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectDir $script)
}

switch ($Target.ToLower()) {
    'help'       { Show-Help }
    'install'    { & $Python -m pip install -e . }
    'dev'        { & $Python -m pip install -e .; & $Python -m pip install pytest }
    'test'       { & $Python -m pytest -q }
    'lint'       { & $Python -m ruff check . }
    'lint-fix'   { & $Python -m ruff check . --fix }
    'compile'    { & $Python -m compileall -q engine gui cli }
    'bump-version' {
        if (-not $env:VERSION) {
            Write-Host "Usage: `$env:VERSION='x.y.z'; pwsh make.ps1 bump-version" -ForegroundColor Red
            exit 2
        }
        & $Python (Join-Path $ProjectDir 'tools/bump_version.py') $env:VERSION
    }
    'clean'      { Invoke-Ps1 'clean.ps1' }
    'clean-deep' { powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectDir 'clean.ps1') -Deep }
    'build'      { Invoke-Ps1 'build_binary.ps1' }
    'package'    { Invoke-Ps1 'package_windows.ps1' }
    'msix'       { Invoke-Ps1 'build_msix.ps1' }
    'dist'       {
        Invoke-Ps1 'clean.ps1'
        Invoke-Ps1 'build_binary.ps1'
        Invoke-Ps1 'package_windows.ps1'
        Write-Host "[OK] Fresh build + package complete." -ForegroundColor Green
    }
    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        Show-Help
        exit 2
    }
}
