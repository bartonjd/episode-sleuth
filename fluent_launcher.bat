@echo off
REM =====================================================================
REM  DVD Episode Identifier - Fluent GUI launcher (Windows 11)
REM  Double-click this file to open the modern Fluent Design app.
REM =====================================================================
cd /d "%~dp0"

REM Use pythonw (no console window) when available; fall back to python.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw dvd_identifier_fluent.py
    goto :eof
)

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 dvd_identifier_fluent.py
) else (
    python dvd_identifier_fluent.py
)

if %errorlevel% neq 0 (
    echo.
    echo The app exited with an error. Make sure Python 3 is installed and that
    echo you have run:  pip install -r requirements.txt
    echo The Fluent GUI also needs:  pip install PySide6-Fluent-Widgets
    echo For fpcalc setup see INSTALL_WINDOWS.md
    pause
)
