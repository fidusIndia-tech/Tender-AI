@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=python
if exist "..\.venv\Scripts\python.exe" set PYTHON_EXE=..\.venv\Scripts\python.exe
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=.venv\Scripts\python.exe

echo Running the GeM tender result check now...
echo (An Edge window may open while it checks results. Please wait.)
echo.

"%PYTHON_EXE%" watcher.py --run-now

echo.
echo Result check finished. You can close this window.
pause
