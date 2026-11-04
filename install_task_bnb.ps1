# Tao Scheduled Task "HLBot BNB X5" (chay run_bnb.ps1 khi dang nhap, an cua so, tu chay lai) o trang thai DISABLED (khoa). Nut BAT BOT moi bat task.
# Khong dong vao task 'HLBot X5' (Hyperliquid) hay cai dat nguon dien.
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = 'HLBot BNB X5'
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dir\run_bnb.ps1`"" -WorkingDirectory $dir
$user = "$env:USERDOMAIN\$env:USERNAME"
$trg = @(New-ScheduledTaskTrigger -AtLogOn -User $user)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Hidden
$prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $act -Trigger $trg -Settings $set -Principal $prin -Description 'Bot LIVE Binance USDT-M BASE_SL250_X5 (run_bnb.ps1: auto-restart + watchdog)'
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Disable-ScheduledTask -TaskName $name | Out-Null
Get-ScheduledTask -TaskName $name | Format-List TaskName,State
