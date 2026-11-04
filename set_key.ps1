$ErrorActionPreference='Stop'
$d='C:\Users\admin\hlbot_bnb_x5\secrets'
Write-Host 'NHAP API KEY BINANCE (Futures only, tat rut tien)' -ForegroundColor Yellow
$k=Read-Host 'API Key'
$s=Read-Host 'Secret Key' -AsSecureString
$sp=[Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
if(-not $k -or -not $sp){Write-Host 'Thieu key, khong luu.' -ForegroundColor Red; pause; exit 1}
Set-Content "$d\binance_api_key.txt" $k.Trim() -NoNewline
Set-Content "$d\binance_api_secret.txt" $sp.Trim() -NoNewline
icacls $d /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
Write-Host 'Da luu key. Co the dong cua so.' -ForegroundColor Green
pause
