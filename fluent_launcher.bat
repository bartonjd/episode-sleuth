@echo off
REM =====================================================================
REM  DVD Episode Identifier - Fluent GUI launcher (Windows 11)
REM  Double-click this file to open the modern Fluent Design app.
REM =====================================================================
REM The GUI now lives in the audio_fingerprint.gui package, so we launch it
REM with "python -m audio_fingerprint.gui". That requires the PARENT of this
REM folder to be the working directory (this folder must be named
REM "audio_fingerprint"), so cd there first.
cd /d "%~dp0.."

REM Use pythonw (no console window) when available; fall back to python.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw -m audio_fingerprint.gui
    goto :eof
)

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m audio_fingerprint.gui
) else (
    python -m audio_fingerprint.gui
)

if %errorlevel% neq 0 (
    echo.
    echo The app exited with an error. Make sure Python 3 is installed and that
    echo you have run:  pip install -r requirements.txt
    echo The Fluent GUI also needs:  pip install PySide6-Fluent-Widgets
    echo For Vosk speech-to-text model setup see INSTALL_WINDOWS.md
    pause
)
