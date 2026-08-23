@echo off
REM =====================================================================
REM  DVD Episode Identifier - GUI launcher (Windows)
REM  Double-click this file to open the point-and-click app.
REM =====================================================================
cd /d "%~dp0"

REM Prefer the Python launcher, fall back to python on PATH.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 dvd_identifier_gui.py
) else (
    python dvd_identifier_gui.py
)

if %errorlevel% neq 0 (
    echo.
    echo The app exited with an error. Make sure Python 3 is installed and that
    echo you have run:  pip install -r requirements.txt
    echo For fpcalc setup see INSTALL_WINDOWS.md
    pause
)
