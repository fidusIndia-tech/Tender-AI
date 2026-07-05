@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   GeM Tender Agent - one-time setup
echo ==================================================
echo.

REM 1) Require Python
where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed.
  echo.
  echo     Please install Python 3.10 or newer from:
  echo         https://www.python.org/downloads/
  echo     During install, TICK "Add python.exe to PATH".
  echo     Then run this file again.
  echo.
  pause
  exit /b 1
)

REM 2) Create the local environment
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating Python environment...
  python -m venv .venv
)

echo [*] Installing components ^(this can take a minute^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [X] Failed to install components. Check your internet connection and retry.
  pause
  exit /b 1
)

REM 3) Create .env from template if missing
if not exist ".env" (
  echo [*] Creating settings file .env from template...
  copy /y ".env.example" ".env" >nul
  echo.
  echo     IMPORTANT: open .env in Notepad and set LOCAL_AGENT_API_KEY to the
  echo     value given to you, and confirm TENDER_AI_BASE_URL is your Tender AI
  echo     website address. Save the file, then run this installer again.
  echo.
  notepad ".env"
  pause
  exit /b 0
)

REM 4) Auto-start hidden at every login (no admin needed - uses Startup folder)
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
echo [*] Enabling automatic background start at login...
> "%STARTUP%\GeMTenderAgent.vbs" echo CreateObject("WScript.Shell").Run "wscript.exe ""%~dp0run-agent-hidden.vbs""", 0, False

REM 5) Start it now
echo [*] Starting the GeM agent in the background...
wscript.exe "%~dp0run-agent-hidden.vbs"

echo.
echo ==================================================
echo   Done.
echo   The GeM agent is now running in the background and
echo   will start automatically each time you log in.
echo   Just use the Tender AI website - click "Run Search
echo   Now" and results will appear there.
echo ==================================================
echo.
pause
