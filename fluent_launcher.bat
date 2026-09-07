@echo off
REM =====================================================================
REM  EpisodeSleuth - Fluent GUI launcher (Windows 11)
REM  Double-click this file to open the modern Fluent Design app.
REM =====================================================================
REM The GUI is launched with "python -m gui" from THIS folder (the one that
REM contains the "gui" package). gui\__main__.py bootstraps sys.path itself, so
REM this works no matter what the containing folder is called (audio_fingerprint,
REM episode-sleuth, episode-sleuth-main, ...). Do NOT cd to the parent and use
REM "python -m audio_fingerprint.gui" - that only worked when the folder was
REM named exactly "audio_fingerprint" and broke for cloned/downloaded copies.
REM The window / taskbar icon is set in code from packaging\app.ico (see
REM gui\main_window.py); no icon needs to be passed on the command line.
cd /d "%~dp0"

REM Use pythonw (no console window) when available; fall back to python.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw -m gui
    goto :eof
)

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m gui
) else (
    python -m gui
)

if %errorlevel% neq 0 (
    echo.
    echo The app exited with an error. Make sure Python 3 is installed and that
    echo you have run:  pip install -r requirements.txt
    echo The Fluent GUI also needs:  pip install PySide6-Fluent-Widgets
    echo For Vosk speech-to-text model setup see INSTALL_WINDOWS.md
    pause
)
