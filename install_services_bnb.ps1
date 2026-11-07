param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$nssm = (Get-Command nssm.exe -ErrorAction Stop).Source
$python = Join-Path $dir '.venv\Scripts\python.exe'
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$services = @(
    @{Name='BaseAsterTrade'; App=$powershell; Args="-NoProfile -ExecutionPolicy Bypass -File `"$dir\run_bnb.ps1`""},
    @{Name='BaseAsterTelegram'; App=$powershell; Args="-NoProfile -ExecutionPolicy Bypass -File `"$dir\run_tg_poller.ps1`""},
    @{Name='BaseAsterDashboard'; App=$python; Args='-u dashboard.py --plain'},
    @{Name='BaseAsterMonitor'; App=$python; Args='-u monitor_bnb.py'}
)
foreach ($file in @($python, "$dir\run_bnb.ps1", "$dir\run_tg_poller.ps1", "$dir\dashboard.py", "$dir\monitor_bnb.py", "$dir\mon_common.py", "$dir\reporting.py",
    "$dir\secrets\binance_api_key.txt", "$dir\secrets\binance_api_secret.txt", "$dir\secrets\telegram.env")) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing required file: $file" }
}
if ($ValidateOnly) { Write-Output 'Service prerequisites OK (4 services).'; exit 0 }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this installer as Administrator.' }
New-Item -ItemType Directory -Path "$dir\logs" -Force | Out-Null
$report = "$dir\logs\service-install-result.json"
function NssmCommand([string[]]$Arguments) {
    $output = & $nssm @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "NSSM failed ($($Arguments -join ' ')): $output" }
}
try {
    # Configure every service before retiring the old process launchers.
    foreach ($s in $services) {
        if (-not (Get-Service -Name $s.Name -ErrorAction SilentlyContinue)) {
            NssmCommand -Arguments @('install', $s.Name, $s.App)
        }
        NssmCommand -Arguments @('set', $s.Name, 'Application', $s.App)
        NssmCommand -Arguments @('set', $s.Name, 'AppDirectory', $dir)
        NssmCommand -Arguments @('set', $s.Name, 'AppParameters', $s.Args)
        NssmCommand -Arguments @('set', $s.Name, 'Start', 'SERVICE_AUTO_START')
        NssmCommand -Arguments @('set', $s.Name, 'AppExit', 'Default', 'Restart')
        NssmCommand -Arguments @('set', $s.Name, 'AppRestartDelay', '15000')
        NssmCommand -Arguments @('set', $s.Name, 'AppKillProcessTree', '1')
        NssmCommand -Arguments @('set', $s.Name, 'AppEnvironmentExtra', 'PYTHONUTF8=1', 'PYTHONIOENCODING=utf-8')
        NssmCommand -Arguments @('set', $s.Name, 'AppStdout', "$dir\logs\$($s.Name).log")
        NssmCommand -Arguments @('set', $s.Name, 'AppStderr', "$dir\logs\$($s.Name).error.log")
        NssmCommand -Arguments @('set', $s.Name, 'AppRotateFiles', '1')
        NssmCommand -Arguments @('set', $s.Name, 'AppRotateOnline', '1')
        NssmCommand -Arguments @('set', $s.Name, 'AppRotateBytes', '5242880')
        & sc.exe failure $s.Name reset= 86400 actions= restart/15000/restart/30000/restart/60000 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Cannot configure recovery for $($s.Name)" }
    }
    foreach ($taskName in @('HLBot BNB X5', 'HLBot_TG_Poller')) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            $backup = "$dir\logs\$taskName.before-services.xml"
            if (-not (Test-Path -LiteralPath $backup)) { Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding Unicode }
            Disable-ScheduledTask -TaskName $taskName | Out-Null
            Stop-ScheduledTask -TaskName $taskName
        }
    }
    # Retire only this project's unmanaged trade/poller trees. The visible dashboard may stay open.
    $allProcesses = @(Get-CimInstance Win32_Process)
    $managedRoots = @(Get-CimInstance Win32_Service | Where-Object { $_.Name -in $services.Name -and $_.ProcessId -gt 0 } | ForEach-Object { $_.ProcessId })
    $managedIds = @($managedRoots)
    do {
        $children = @($allProcesses | Where-Object { $_.ParentProcessId -in $managedIds -and $_.ProcessId -notin $managedIds } | ForEach-Object { $_.ProcessId })
        $managedIds += $children
    } while ($children.Count -gt 0)
    $existing = @($allProcesses | Where-Object {
        $_.ProcessId -notin $managedIds -and
        $_.ProcessId -ne $PID -and $_.CommandLine -and (
            ($_.Name -eq 'python.exe' -and $_.CommandLine.Contains("$dir\.venv\Scripts\python.exe") -and $_.CommandLine -match '\b(bnb_bot|tg_live_poller)\.py\b') -or
            ($_.Name -in @('powershell.exe','pwsh.exe') -and $_.CommandLine.Contains($dir) -and $_.CommandLine -match '\\run_(bnb|tg_poller)\.ps1\b')
        )
    })
    $existingIds = @($existing.ProcessId)
    foreach ($p in $existing) {
        if ($p.ParentProcessId -notin $existingIds -and (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) {
            & taskkill.exe /F /T /PID $p.ProcessId | Out-Null
            if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) { throw "Cannot retire old process $($p.ProcessId)" }
        }
    }
    foreach ($s in $services) {
        Start-Service -Name $s.Name
        (Get-Service -Name $s.Name).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
    @{Status='Complete'; Time=(Get-Date -Format o); Services=@($services.Name)} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding UTF8
} catch {
    @{Status='Failed'; Time=(Get-Date -Format o); Error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding UTF8
    throw
}
