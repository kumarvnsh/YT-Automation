# Preserve Task Scheduler credentials. Stop only stale monitoring and the old UI.
$ErrorActionPreference='Stop'
$Admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Admin) { throw 'Administrator PowerShell is required to stop the older scheduled-task processes.' }
$StoppedMonitors=0
$StoppedDashboards=0
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -match 'scripts[/\\]operations_tick.py' -and $_.CreationDate -lt (Get-Date).AddMinutes(-10)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $StoppedMonitors++ }
$NeedRestart=$false
try {
    $Key=(Get-Content -LiteralPath 'D:\histold\dashboard_key.txt' -Raw).Trim()
    Invoke-WebRequest 'http://127.0.0.1:8799/api/bots?channel=histold' -Headers @{Cookie="hk=$Key"} -UseBasicParsing -TimeoutSec 5 | Out-Null
} catch { if ([int]$_.Exception.Response.StatusCode -eq 404) { $NeedRestart=$true } }
if ($NeedRestart) {
    foreach ($Listener in @(Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue)) {
        $Old=Get-CimInstance Win32_Process -Filter ('ProcessId='+[string]$Listener.OwningProcess)
        if ($Old.Name -eq 'python.exe' -and $Old.CommandLine -match 'scripts[/\\]dashboard_server.py' -and $Old.CommandLine -match '8799') {
            Stop-Process -Id $Old.ProcessId -Force
            $StoppedDashboards++
        }
    }
}
Start-ScheduledTask -TaskName 'Histold Jarvis Operations'
Write-Output "Stopped $StoppedMonitors stale monitoring process(es) and $StoppedDashboards old dashboard instance(s)."
if ($NeedRestart -and $StoppedDashboards -eq 0) { Write-Warning 'The protected old dashboard could not be matched automatically; inspect the port 8799 owner before restarting it.' }
Write-Output 'Task credentials were not changed. No capture or upload worker was stopped.'
Write-Output 'The assistant can now relaunch the dashboard in the normal Windows account.'
