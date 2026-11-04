# Chay bot LIVE Binance USDT-M (BNB X5) tren Windows: tu khoi dong lai sau 30s neu bot thoat + watchdog state_live.json cu > 600s.
# Mac dinh la LENH THAT (BNB_DRY_RUN=0). Chay thu khong gui lenh:  powershell -File run_bnb.ps1 -Dry
# Khoa: nut "BNB X5 - KHAN CAP DONG VI THE" (KILL + dong vi the). Khoa API: secrets\binance_api_key.txt + binance_api_secret.txt. KHONG in khoa ra log.
param([switch]$Dry)
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
if ($Dry) { $env:BNB_DRY_RUN = '1' } else { $env:BNB_DRY_RUN = '0' }
$StaleS = 600
$py = Join-Path $dir '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$PidFile = Join-Path $dir 'run_bnb.pid'; $BotPidFile = Join-Path $dir 'bot_bnb.pid'; $Kill = Join-Path $dir 'KILL'
function Log($m) { try { Add-Content -Path (Join-Path $dir 'bot_live.log') -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding utf8 -ErrorAction Stop } catch {} }
# AN TOAN: chi chay LENH THAT khi co ALLOW_PC_RUN.flag (do nut BAT BOT tao). Dry-run thi khong can.
if (-not $Dry -and -not (Test-Path (Join-Path $dir 'ALLOW_PC_RUN.flag'))) { Log 'BLOCKED: thieu ALLOW_PC_RUN.flag -> khong khoi dong bot LIVE'; exit 0 }
if (-not $Dry -and -not $env:BNB_API_KEY -and -not ((Test-Path (Join-Path $dir 'secrets\binance_api_key.txt')) -and (Test-Path (Join-Path $dir 'secrets\binance_api_secret.txt')))) { Log 'STOP: chua co file khoa API Binance trong secrets'; exit 2 }
if (Test-Path $PidFile) {
  $old = 0; [int]::TryParse(((Get-Content $PidFile -ErrorAction SilentlyContinue) -join ''), [ref]$old) | Out-Null
  if ($old -gt 0 -and $old -ne $PID -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { Write-Host "run_bnb.ps1 dang chay (pid $old). Thoat."; exit 0 }
}
Set-Content -Path $PidFile -Value $PID -Encoding ascii
try {
  while ($true) {
    if (Test-Path $Kill) { Log 'KILL file ton tai -> khong chay bot.'; break }
    if (-not $Dry) { $rs = (& $py rescale_config.py 2>&1) -join ' '; Log ("config: {0}" -f $rs) }
    Log ("run_bnb.ps1: starting bnb_bot.py (BNB_DRY_RUN={0})" -f $env:BNB_DRY_RUN)
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$py`" -u bnb_bot.py >> bot_live.log 2>&1" -WorkingDirectory $dir -WindowStyle Hidden -PassThru
    Set-Content -Path $BotPidFile -Value $p.Id -Encoding ascii
    $t0 = Get-Date
    while (-not $p.HasExited) {
      Start-Sleep -Seconds 30
      if ($p.HasExited) { break }
      if (Test-Path $Kill) { Start-Sleep -Seconds 60; if (-not $p.HasExited) { Log 'KILL: bot chua thoat sau 60s -> taskkill'; & taskkill /F /T /PID $p.Id | Out-Null }; break }
      $sf = Join-Path $dir 'state_live.json'
      if (Test-Path $sf) {
        $age = ((Get-Date) - (Get-Item $sf).LastWriteTime).TotalSeconds; $up = ((Get-Date) - $t0).TotalSeconds
        if ($age -gt $StaleS -and $up -gt $StaleS) { Log ("WATCHDOG: state_live.json stale {0:N0}s -> kill bot, restart" -f $age); & taskkill /F /T /PID $p.Id | Out-Null }
      }
    }
    Log ("bot exited code {0}" -f $p.ExitCode)
    if (Test-Path $Kill) { Log 'KILL -> dung vong lap'; break }
    Log 'restarting in 30s'; Start-Sleep -Seconds 30
  }
} finally {
  if ($p -and -not $p.HasExited) { & taskkill /F /T /PID $p.Id | Out-Null }
  Remove-Item $PidFile, $BotPidFile -ErrorAction SilentlyContinue
}
