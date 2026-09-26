@echo off
cd /d "%~dp0"
title install-service

rem  Pure ASCII on purpose - see start.cmd for why.

fltmc >nul 2>&1
if not errorlevel 1 goto run

echo Requesting administrator privileges...
echo A UAC prompt will appear. Enter your own Windows credentials.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if errorlevel 1 (
    echo.
    echo [X] Elevation was cancelled or failed.
    echo     Alternative: right-click this file and choose "Run as administrator".
    echo.
    pause
)
exit /b

:run
echo Running with administrator privileges...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0install-service.ps1"
echo.
echo Finished. You can close this window.
pause
