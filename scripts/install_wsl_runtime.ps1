# Run in Administrator PowerShell after the WSL feature-enabling restart.
$ErrorActionPreference='Stop'
$Admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Admin) { throw 'Open PowerShell as Administrator to install the Microsoft WSL runtime package.' }
$Installer='D:\histold\tools\wsl.3.0.5.0.x64.msi'
if (-not (Test-Path $Installer)) { throw 'The downloaded WSL installer is missing.' }
$Signature=Get-AuthenticodeSignature -LiteralPath $Installer
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'The installer Microsoft signature could not be verified.' }
$Install=Start-Process -FilePath msiexec.exe -ArgumentList @('/i',('"'+$Installer+'"'),'/qn','/norestart','/l*v','D:\histold\wsl-install.log') -WindowStyle Hidden -Wait -PassThru
if ($Install.ExitCode -notin @(0,3010)) { throw "WSL installation failed (exit $($Install.ExitCode)); inspect D:\histold\wsl-install.log." }
Write-Output 'The Microsoft WSL runtime package is installed.'
if ($Install.ExitCode -eq 3010) { Write-Output 'Windows reports another restart is required. Save work before restarting.' }
else { & wsl.exe --version; Write-Output 'Ready for the Ubuntu/Superbot setup step in your normal Windows account.' }
# Keep the existing task and its saved credentials unchanged. The shared dashboard
# launcher chooses the installed checkout without editing Task Scheduler actions.
# A bounded monitoring tick cannot legitimately keep running for ten minutes.
# Remove only stale copies of that exact worker, never publishing or capture jobs.
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -match 'scripts[/\\]operations_tick.py' -and $_.CreationDate -lt (Get-Date).AddMinutes(-10)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName 'Histold Jarvis Operations'
# Stop only the verified old dashboard when the Bot workspace route is missing.
$NeedRestart=$false
try {
    $Key=(Get-Content -LiteralPath 'D:\histold\dashboard_key.txt' -Raw).Trim()
    Invoke-WebRequest 'http://127.0.0.1:8799/api/bots?channel=histold' -Headers @{Cookie="hk=$Key"} -UseBasicParsing -TimeoutSec 5 | Out-Null
} catch { if ([int]$_.Exception.Response.StatusCode -eq 404) { $NeedRestart=$true } }
if ($NeedRestart) {
    $Listener=Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue
    foreach ($Entry in $Listener) {
        $OldDashboard=Get-CimInstance Win32_Process -Filter "ProcessId=$($Entry.OwningProcess)"
        if ($OldDashboard.CommandLine -match 'scripts[/\\]dashboard_server.py' -and $OldDashboard.CommandLine -match '8799') {
            Stop-Process -Id $OldDashboard.ProcessId -Force
        }
    }
    Write-Output 'The old dashboard process was stopped. Start scripts/start_dashboard.ps1 from your normal PowerShell account.'
}
