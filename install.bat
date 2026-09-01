@echo off
REM =====================================================================
REM  EpisodeSleuth - one-click Windows setup
REM  Double-click this file. It just runs install.ps1 with the right
REM  execution policy so you do not have to type any PowerShell flags.
REM =====================================================================
cd /d "%~dp0"
echo Running setup (this installs Python deps, ffmpeg, Vosk model and a shortcut)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
if %errorlevel% neq 0 (
    echo Setup reported an error. See the messages above.
) else (
    echo Setup finished. You can close this window.
)
pause
