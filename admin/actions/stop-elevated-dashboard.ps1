# Stop the dashboard on port 8799 (and its venv launcher) when it was started as Administrator,
# so the Telegram bot can restart it normally. Touches only python processes running dashboard_server.py.
$Stopped = @()
foreach ($Listener in @(Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue)) {
    $Proc = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [string]$Listener.OwningProcess)
    if (-not $Proc -or $Proc.Name -ne 'python.exe' -or $Proc.CommandLine -notmatch 'dashboard_server\.py') {
        "Port 8799 is held by something else ($($Proc.Name) $($Proc.ProcessId)); left running."
        continue
    }
    $Parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [string]$Proc.ParentProcessId) -ErrorAction SilentlyContinue
    Stop-Process -Id $Proc.ProcessId -Force
    $Stopped += $Proc.ProcessId
    if ($Parent -and $Parent.Name -eq 'python.exe' -and $Parent.CommandLine -match 'dashboard_server\.py') {
        Stop-Process -Id $Parent.ProcessId -Force -ErrorAction SilentlyContinue
        $Stopped += $Parent.ProcessId
    }
}
if ($Stopped) { "Stopped dashboard process(es): $($Stopped -join ', ')" } else { 'No dashboard was listening on port 8799.' }
