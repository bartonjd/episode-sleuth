@echo off
REM ============================================================
REM  install_chromaprint.bat
REM  Simple checker / helper for Chromaprint's fpcalc.exe
REM  (Windows). Double-click this file or run it from a terminal.
REM ============================================================
setlocal EnableDelayedExpansion

echo ============================================================
echo    Chromaprint (fpcalc) checker for Windows
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "FOUND="

REM ---- 1. Is fpcalc on the PATH? ----
echo ==^> Checking whether fpcalc is on your PATH...
where fpcalc >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [ OK ] fpcalc was found on your PATH.
    echo.
    fpcalc -version
    set "FOUND=1"
    goto :verified
)
echo        Not found on PATH.
echo.

REM ---- 2. Is fpcalc.exe sitting in this project folder? ----
echo ==^> Checking the project folder...
if exist "%SCRIPT_DIR%fpcalc.exe" (
    echo [ OK ] Found fpcalc.exe in the project folder.
    echo.
    "%SCRIPT_DIR%fpcalc.exe" -version
    set "FOUND=1"
    goto :verified
)
echo        Not found in the project folder either.
echo.

REM ---- 3. Not installed: guide the user ----
echo ============================================================
echo    fpcalc.exe is NOT installed yet.
echo ============================================================
echo.
echo You have two easy options:
echo.
echo   OPTION A ^(automatic^): run the PowerShell installer
echo   ---------------------------------------------------------
echo   It downloads and installs fpcalc.exe for you. Run:
echo.
echo       powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_chromaprint.ps1"
echo.
echo   OPTION B ^(manual^): download it yourself
echo   ---------------------------------------------------------
echo   1. Open this page in your browser:
echo          https://acoustid.org/chromaprint
echo      ^(or  https://github.com/acoustid/chromaprint/releases ^)
echo   2. Download the Windows file, named like:
echo          chromaprint-fpcalc-1.6.0-windows-x86_64.zip
echo   3. Right-click the .zip - Extract All...
echo   4. Copy fpcalc.exe into THIS folder:
echo          %SCRIPT_DIR%
echo      ^(or add its folder to your PATH - see INSTALL_WINDOWS.md^)
echo   5. Run this .bat file again to verify.
echo.
echo For full step-by-step help, open INSTALL_WINDOWS.md
echo.

REM ---- Offer to launch the PowerShell installer automatically ----
set /p "RUNPS=Run the automatic PowerShell installer now? (Y/N): "
if /I "!RUNPS!"=="Y" (
    echo.
    echo Launching PowerShell installer...
    powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_chromaprint.ps1"
    echo.
    REM Re-check after the installer runs.
    where fpcalc >nul 2>nul
    if !ERRORLEVEL!==0 (
        set "FOUND=1"
        goto :verified
    )
    if exist "%SCRIPT_DIR%fpcalc.exe" (
        set "FOUND=1"
        goto :verified
    )
)
goto :end

:verified
echo.
echo ============================================================
echo [ OK ] fpcalc is installed and working. You're all set!
echo        You can now use the --acoustic and --both features.
echo ============================================================
goto :end

:end
echo.
pause
endlocal
