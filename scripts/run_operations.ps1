param([string]$DataRoot = 'D:\histold\YT-Automation')
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$HomeDir = Split-Path -Parent $DataRoot
$env:PYTHONUTF8='1'
# Service recovery is handled and reported by operations_tick; a failed gateway
# must not prevent analytics monitoring from running.
$Stdout=Join-Path $HomeDir 'operations-child.log'
$Stderr=Join-Path $HomeDir 'operations-child.err'
$Worker=Start-Process -FilePath (Join-Path $DataRoot '.venv\Scripts\python.exe') `
    -ArgumentList @(('"'+(Join-Path $Repo 'scripts\operations_tick.py')+'"'),'--data-root',('"'+$DataRoot+'"')) `
    -WorkingDirectory $Repo -WindowStyle Hidden -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
# Stop the owned process tree before Task Scheduler's five-minute cutoff. Killing
# only its PowerShell parent leaves a Python orphan holding operations.lock.
if (-not $Worker.WaitForExit(240000)) {
    & taskkill.exe /PID $Worker.Id /T /F | Out-Null
    Add-Content -LiteralPath (Join-Path $HomeDir 'operations.log') -Value 'Operations check exceeded four minutes; its owned worker tree was stopped.'
    exit 1
}
$Worker.Refresh()
foreach ($Output in @($Stdout,$Stderr)) {
    if (Test-Path $Output) { Get-Content -LiteralPath $Output | Add-Content -LiteralPath (Join-Path $HomeDir 'operations.log') }
}
exit $Worker.ExitCode
