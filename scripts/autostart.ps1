# Login autostart (Task Scheduler "Histold Dashboard", at logon): dashboard,
# WhatsApp gateway, Shotsnip clipping worker, then open the dashboard in the browser.
# Each step is independent so one failure does not block the others.
param(
    [string]$HomeDir = 'D:\histold',
    [int]$Port = 8799,
    [switch]$NoBrowser
)
$Log = Join-Path $HomeDir 'autostart.log'
function Step($Name, [scriptblock]$Body) {
    try { $Out = & $Body 2>&1 | Out-String; Add-Content $Log "$(Get-Date -Format s) ok   $Name $($Out.Trim())" }
    catch { Add-Content $Log "$(Get-Date -Format s) FAIL $Name $($_.Exception.Message)" }
}
# Run a child script without capturing its output: the dashboard/gateway it launches
# would inherit a capture pipe and keep us waiting forever. Its own logs say enough.
function Invoke-Script($Path, [string[]]$Arguments = @()) {
    $Info = New-Object System.Diagnostics.ProcessStartInfo 'powershell.exe'
    $Info.Arguments = (@('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', "`"$Path`"") + $Arguments) -join ' '
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Process = [System.Diagnostics.Process]::Start($Info)
    if (-not $Process.WaitForExit(240000)) { throw 'timed out' }
    if ($Process.ExitCode) { throw "exit $($Process.ExitCode)" }
    'done'
}
$Here = $PSScriptRoot
# Keep the installed dashboard on this checkout and the Tailscale proxy's port.
# The strategist clone supplies analytics, not a competing dashboard executable.
$DashboardScript = Join-Path $Here 'start_dashboard.ps1'

Start-Sleep -Seconds 20   # let networking and disks settle after boot
Step 'dashboard' {
    $Up = $false
    try { $Up = (Invoke-WebRequest "http://127.0.0.1:$Port/login" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch {}
    if ($Up) { 'already running' } else { Invoke-Script $DashboardScript }
}
Step 'whatsapp'  { Invoke-Script (Join-Path $Here 'start_openwa.ps1') @('-HomeDir', $HomeDir) }
Step 'shotsnip'  { Invoke-Script (Join-Path $Here 'start_shotsnip_worker.ps1') @('-HomeDir', $HomeDir, '-Port', "$Port", '-Force') }
# Open the installed dashboard app (Brave "Install app", pinned to the taskbar);
# fall back to a normal browser tab if that shortcut is gone.
$App = @("$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Histold.lnk",
         "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Brave Apps\Histold.lnk") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $NoBrowser) { Step 'open app' { if ($App) { Start-Process $App; "opened $App" } else { Start-Process "http://localhost:$Port/"; 'opened browser tab' } } }
