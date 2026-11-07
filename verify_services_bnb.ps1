param([switch]$TestDashboardRecovery)
$ErrorActionPreference = 'Stop'
$names = @('BaseAsterTrade', 'BaseAsterTelegram', 'BaseAsterDashboard', 'BaseAsterMonitor')
foreach ($name in $names) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$name'"
    if ($service.State -ne 'Running' -or $service.StartMode -ne 'Auto') { throw "$name is not Running/Auto" }
}
if ($TestDashboardRecovery) {
    $service = Get-CimInstance Win32_Service -Filter "Name='BaseAsterDashboard'"
    $child = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $service.ProcessId -and $_.Name -eq 'python.exe' })
    if ($child.Count -ne 1) { throw 'Expected one dashboard application process' }
    $oldId = $child[0].ProcessId
    & taskkill.exe /F /T /PID $oldId | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stop dashboard application for recovery test (Administrator required)' }
    $recovered = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        $replacement = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $service.ProcessId -and $_.Name -eq 'python.exe' -and $_.ProcessId -ne $oldId })
        if ($replacement.Count -eq 1) { $recovered = $true; break }
    }
    if (-not $recovered) { throw 'Dashboard did not recover within 30 seconds' }
    @{Test='DashboardRecovery'; Passed=$true; OldPID=$oldId; NewPID=$replacement[0].ProcessId; Time=(Get-Date -Format o)} |
        ConvertTo-Json | Set-Content "$PSScriptRoot\logs\service-recovery-test.json" -Encoding UTF8
}
Write-Output 'All four services are Running/Auto.'
