@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title BNB X5 - DASHBOARD (read-only)
mode con: cols=110 lines=40 >nul 2>&1
if exist ".venv\Scripts\python.exe" ( ".venv\Scripts\python.exe" dashboard.py ) else ( python dashboard.py )
echo.
echo Dashboard closed. The bot keeps running.
pause
