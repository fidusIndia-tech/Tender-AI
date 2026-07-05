@echo off
setlocal
cd /d "%~dp0"

echo Stopping the GeM Tender Agent and disabling auto-start...

REM Remove the login auto-start entry
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\GeMTenderAgent.vbs" del /q "%STARTUP%\GeMTenderAgent.vbs"

REM Stop any running background agent (windowless python)
taskkill /f /im pythonw.exe >nul 2>nul

echo Done. The agent will no longer start automatically.
echo (Your settings in .env are kept.)
pause
