@echo off
rem KHAN CAP (Binance): tao KILL, dung bot BNB, HUY MOI LENH CHO, DONG MOI VI THE (reduce-only). Khong dong vao bot Hyperliquid.
cd /d "%~dp0"
title BNB X5 - KHAN CAP DONG VI THE
set PYTHONUTF8=1
echo Dang dong khan cap bot Binance...
echo killed %date% %time% > KILL
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-ScheduledTask -TaskName 'HLBot BNB X5' -ErrorAction SilentlyContinue; Disable-ScheduledTask -TaskName 'HLBot BNB X5' -ErrorAction SilentlyContinue | Out-Null; Remove-Item 'ALLOW_PC_RUN.flag' -ErrorAction SilentlyContinue; foreach($f in 'run_bnb.pid','bot_bnb.pid'){ if(Test-Path $f){ $id=[int](Get-Content $f); & taskkill /F /T /PID $id | Out-Null } }"
if exist ".venv\Scripts\python.exe" ( ".venv\Scripts\python.exe" emergency.py ) else ( python emergency.py )
echo.
echo Xong. Bot Binance da dung (file KILL). Muon chay lai: nut "BNB X5 - BAT BOT".
pause
