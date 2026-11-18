# Stop Jarvis monitoring ticks (operations_tick.py) stuck for more than 10 minutes.
# Capture, upload and dashboard processes are never touched.
$Old = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -match 'scripts[/\\]operations_tick\.py' -and $_.CreationDate -lt (Get-Date).AddMinutes(-10)
})
foreach ($Proc in $Old) { Stop-Process -Id $Proc.ProcessId -Force }
"Stopped $($Old.Count) stale monitoring process(es)."
