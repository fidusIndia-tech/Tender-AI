@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=python
if exist "..\.venv\Scripts\python.exe" set PYTHON_EXE=..\.venv\Scripts\python.exe
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=.venv\Scripts\python.exe

echo Starting Local GeM Tender Search Agent...
echo Keep this window open on the office/local PC.
echo Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" agent.py --loop --interval-minutes 30
pause
