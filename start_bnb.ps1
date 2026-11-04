# BAT BOT BNB X5: mo khoa + chay bot LIVE Binance that. Exit: 0 da chay | 3 dang chay | 4 chua co key | 5 khong khoi dong duoc | 7 khong co task
# KHONG dong vao bot Hyperliquid (hlbot_x5 / task 'HLBot X5').
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
$T = 'HLBot BNB X5'
function Running { Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and (($_.CommandLine -like '*bnb_bot.py*' -and ($_.CommandLine -like '*hlbot_bnb_x5*' -or $_.ExecutablePath -like '*hlbot_bnb_x5*')) -or $_.CommandLine -like '*hlbot_bnb_x5\run_bnb.ps1*') } }
if (Running) { exit 3 }
if (-not ((Test-Path (Join-Path $dir 'secrets\binance_api_key.txt')) -and (Test-Path (Join-Path $dir 'secrets\binance_api_secret.txt')))) { exit 4 }
if (-not (Get-ScheduledTask -TaskName $T -ErrorAction SilentlyContinue)) { exit 7 }
Remove-Item (Join-Path $dir 'KILL') -ErrorAction SilentlyContinue
Set-Content -Path (Join-Path $dir 'ALLOW_PC_RUN.flag') -Value ("unlocked by BAT BOT {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding ascii
Enable-ScheduledTask -TaskName $T | Out-Null
Start-ScheduledTask -TaskName $T
Start-Sleep -Seconds 10
for ($i = 0; $i -lt 15; $i++) { if (Running) { exit 0 }; Start-Sleep -Seconds 1 }
exit 5
