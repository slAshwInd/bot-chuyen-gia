# Cai lai (dang ky lai) 2 task theo THU MUC HIEN TAI cua file nay. Dung sau khi chuyen folder bot.
# Chay: powershell -ExecutionPolicy Bypass -File install_task_bnb.ps1   (luu y: neu bot dang chay se KHONG bi dung; task moi ap dung lan chay sau)
$d = $PSScriptRoot
$u = "$env:USERDOMAIN\$env:USERNAME"
$pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
$st = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
$a1 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$d\run_bnb.ps1`"" -WorkingDirectory $d
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $u
Register-ScheduledTask -TaskName 'HLBot BNB X5' -Action $a1 -Trigger $t1 -Principal $pr -Settings $st -Force | Out-Null
$a2 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$d\run_tg_poller.ps1`"" -WorkingDirectory $d
Register-ScheduledTask -TaskName 'HLBot_TG_Poller' -Action $a2 -Trigger $t1 -Principal $pr -Settings $st -Force | Out-Null
$a3 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$d\run_monitor.ps1`"" -WorkingDirectory $d
$st3 = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'HLBot_Monitor' -Action $a3 -Trigger $t1 -Principal $pr -Settings $st3 -Force | Out-Null
Write-Host "Da dang ky task HLBot_Monitor (giam sat + canh bao Telegram)" -ForegroundColor Green
Write-Host "Da dang ky 2 task tu thu muc: $d" -ForegroundColor Green
