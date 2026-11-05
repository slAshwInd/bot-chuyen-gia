Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
while ($true) { & .\.venv\Scripts\python.exe -u monitor_bnb.py *>> logs\monitor_stdout.log; Start-Sleep 20 }
