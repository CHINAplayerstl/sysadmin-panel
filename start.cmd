@echo off
cd /d "%~dp0"
title sysadmin-panel

rem ---------------------------------------------------------------
rem  Pure ASCII on purpose: cmd.exe reads .cmd files using the OEM
rem  code page, so non-ASCII bytes here would corrupt the parser and
rem  make the window flash and close instantly.
rem  Pass extra args through, e.g.:  start.cmd --port 8788
rem ---------------------------------------------------------------

where python >nul 2>&1
if errorlevel 1 (
    echo [X] python not found in PATH.
    echo     Install Python 3 and make sure it is on PATH.
    pause
    exit /b 1
)

echo Starting system admin panel...
echo (press Ctrl+C to stop)
echo.

python run.py %*

echo.
echo Panel stopped.
pause
