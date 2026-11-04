@echo off
cd /d "%~dp0"
title BNB X5 - BAT BOT (Binance)
set PYTHONUTF8=1
echo ============================================================
echo   BNB X5 - BAT BOT (Binance USDT-M Futures)
echo ============================================================
echo.
echo Mo khoa (xoa KILL, tao ALLOW_PC_RUN.flag, bat task HLBot BNB X5) va khoi dong bot LIVE Binance...
echo Vui long doi khoang 10-20 giay...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bnb.ps1"
set RC=%errorlevel%
if "%RC%"=="0" goto started
if "%RC%"=="3" goto already
if "%RC%"=="4" goto nokey
if "%RC%"=="7" goto notask
goto failed

:already
echo Bot Binance dang chay roi, khong khoi dong them (tranh chay 2 bot).
echo Xem: nut "BNB X5 - THEO DOI".
pause
exit /b 3

:nokey
echo Chua co API key Binance. Anh bam nut "BNB X5 - NHAP API KEY" truoc, roi bam lai nut nay.
echo (Khong co gi duoc khoi dong.)
pause
exit /b 4

:notask
echo Khong thay Scheduled Task "HLBot BNB X5". Anh nhan Chuyen gia.
pause
exit /b 7

:failed
echo Chua thay bot khoi dong (ma %RC%). Anh bam nut "BNB X5 - THEO DOI" de xem, hoac nhan Chuyen gia.
pause
exit /b 5

:started
echo Da khoi dong bot Binance tren PC. Mo nut "BNB X5 - THEO DOI" de xem chi tiet.
pause
exit /b 0
