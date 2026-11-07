param([switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$dir=$PSScriptRoot
$nssm=(Get-Command nssm.exe -ErrorAction Stop).Source
$py=Join-Path $dir '.venv\Scripts\python.exe'
foreach ($file in @($py,"$dir\service_supervisor.py","$dir\mon_common.py","$dir\monitor_bnb.py","$dir\tg_live_poller.py","$dir\ALLOW_PC_RUN.flag")) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing prerequisite: $file. Run only from runtime, not upcode." }
}
if ($ValidateOnly) { 'Prerequisites OK; no credentials inspected.'; exit 0 }
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Administrator approval required.' }
function Ns([string[]]$A) {
    $out=& $nssm @A 2>&1
    if ($LASTEXITCODE -ne 0) { throw "NSSM failed: $($A[0]) $($A[1])" }
}
New-Item -ItemType Directory "$dir\logs" -Force | Out-Null
$result="$dir\logs\unified-migration-result.json"
try {
    $svc=Get-Service BaseAsterTrade -ErrorAction Stop
    if ((& $nssm get BaseAsterTrade AppParameters) -match 'service_supervisor') { throw 'Host already configured; inspect before any retry.' }
    if (Test-Path "$dir\KILL") { throw 'KILL is present; never automatically unlock.' }
    $line=Get-Content "$dir\bot_live.log" -Tail 100 | Where-Object { $_ -match '^LOOP ' } | Select-Object -Last 1
    if ($line -notmatch 'sync_ok=True halted=None.*pos=\[\] stops=\[\]') { throw 'Fresh flat synced LOOP required before stopping.' }
    $stamp=[datetime]::ParseExact($line.Substring(5,19),'yyyy-MM-dd HH:mm:ss',[cultureinfo]::InvariantCulture)
    if (((Get-Date)-$stamp).TotalSeconds -gt 30) { throw 'LOOP stale; abort before service changes.' }
    $hash=(Get-FileHash "$dir\config_live.json").Hash
    # New Trade cannot start until human verifies account AFTER the old Trade stops.
    'Awaiting owner post-stop exchange verification; do not remove automatically.' | Set-Content "$dir\SERVICE_MIGRATION_HOLD.flag" -Encoding ascii
    foreach ($name in @('HLBot BNB X5','HLBot_TG_Poller','HLBot_Monitor')) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $name | Out-Null
            if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') { Stop-ScheduledTask -TaskName $name }
        }
    }
    foreach ($name in @('BaseAsterTelegram','BaseAsterMonitor','BaseAsterDashboard','BaseAsterTrade')) {
        $s=Get-Service $name -ErrorAction SilentlyContinue
        if ($s -and $s.Status -ne 'Stopped') {
            Stop-Service $name
            (Get-Service $name).WaitForStatus('Stopped',[timespan]::FromSeconds(45))
        }
    }
    $left=@(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and
        $_.CommandLine -match '(bnb_bot|tg_live_poller|monitor_bnb|dashboard)\.py' -and
        ($_.ExecutablePath.StartsWith($dir) -or $_.CommandLine.Contains($dir))
    })
    if ($left.Count) { throw 'Unmanaged child remains; no duplicate launch.' }
    if ((Get-FileHash "$dir\config_live.json").Hash -ne $hash) { throw 'Config changed during migration; hold retained.' }
    Ns @('set','BaseAsterTrade','Application',$py)
    Ns @('set','BaseAsterTrade','AppDirectory',$dir)
    Ns @('set','BaseAsterTrade','AppParameters','-u service_supervisor.py')
    Ns @('set','BaseAsterTrade','DisplayName','BaseAster - Trade, Telegram, Monitor')
    Ns @('set','BaseAsterTrade','Start','SERVICE_AUTO_START')
    Ns @('set','BaseAsterTrade','AppNoConsole','1')
    Ns @('set','BaseAsterTrade','AppExit','Default','Restart')
    Ns @('set','BaseAsterTrade','AppRestartDelay','15000')
    Ns @('set','BaseAsterTrade','AppKillProcessTree','1')
    # Existing NSSM environment MUST stay untouched (may contain private settings).
    Ns @('set','BaseAsterTrade','AppStdout',"$dir\logs\service-host.log")
    Ns @('set','BaseAsterTrade','AppStderr',"$dir\logs\service-host.error.log")
    Ns @('set','BaseAsterTrade','AppRotateFiles','1')
    Ns @('set','BaseAsterTrade','AppRotateOnline','1')
    Ns @('set','BaseAsterTrade','AppRotateBytes','5242880')
    & sc.exe failure BaseAsterTrade reset= 86400 actions= restart/15000/restart/30000/restart/60000 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'SC recovery setup failed.' }
    foreach ($name in @('BaseAsterTelegram','BaseAsterMonitor','BaseAsterDashboard')) {
        if (Get-Service $name -ErrorAction SilentlyContinue) {
            Ns @('set',$name,'AppNoConsole','1')
            Set-Service $name -StartupType Disabled
        }
    }
    Start-Service BaseAsterTrade
    (Get-Service BaseAsterTrade).WaitForStatus('Running',[timespan]::FromSeconds(30))
    @{ok=$true; phase='HostStarted_TradeHeld'; time=(Get-Date).ToString('o'); configPreserved=((Get-FileHash "$dir\config_live.json").Hash -eq $hash)} | ConvertTo-Json | Set-Content $result -Encoding UTF8
} catch {
    @{ok=$false; time=(Get-Date).ToString('o'); error=$_.Exception.Message; note='Inspect before retry; Trade hold retained; no legacy/rescale automatic rollback.'} | ConvertTo-Json | Set-Content $result -Encoding UTF8
    throw
}