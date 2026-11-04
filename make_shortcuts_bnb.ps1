# Tao 3 shortcut Desktop cho bot Binance (shortcut "BNB X5 - NHAP API KEY" da co san, khong tao lai).
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desk = [Environment]::GetFolderPath('Desktop')
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut((Join-Path $desk 'BNB X5 - BAT BOT.lnk'))
$s.TargetPath = 'cmd.exe'; $s.Arguments = "/c `"$dir\start_bnb.bat`""; $s.WorkingDirectory = $dir; $s.Description = 'Bat bot live Binance USDT-M (BASE_SL250_X5)'; $s.IconLocation = 'shell32.dll,44'; $s.Save()
$s = $sh.CreateShortcut((Join-Path $desk 'BNB X5 - KHAN CAP DONG VI THE.lnk'))
$s.TargetPath = 'cmd.exe'; $s.Arguments = "/c `"$dir\emergency_bnb.bat`""; $s.WorkingDirectory = $dir; $s.Description = 'Binance: dung bot + huy lenh + dong het vi the'; $s.IconLocation = 'shell32.dll,27'; $s.Save()
$s = $sh.CreateShortcut((Join-Path $desk 'BNB X5 - THEO DOI.lnk'))
$s.TargetPath = 'cmd.exe'; $s.Arguments = "/c `"$dir\dashboard_bnb.bat`""; $s.WorkingDirectory = $dir; $s.Description = 'Binance: trang thai + PNL + hoat dong (chi doc)'; $s.IconLocation = 'shell32.dll,22'; $s.Save()
Get-ChildItem $desk -Filter 'BNB X5*' | Select-Object -ExpandProperty Name
