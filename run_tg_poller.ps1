Set-Location C:\Users\Admin\hlbot_bnb_x5
while ($true) { & .\.venv\Scripts\python.exe -u tg_live_poller.py *>> tg_poller.log; Start-Sleep 15 }
