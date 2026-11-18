# Install or update the Histold admin runner. Run in an ADMINISTRATOR PowerShell:
#   powershell -ExecutionPolicy Bypass -File D:\YT-Automation\scripts\install_admin_runner.ps1
# It copies admin\runner.ps1 and admin\actions\*.ps1 into D:\histold\admin-runner, which only
# Administrators can change, so no bot or normal-user process can alter what runs elevated.
# Changing the allowlist means editing the repo and re-running this script as Administrator.
# Remove: Unregister-ScheduledTask -TaskName 'Histold Admin Runner' -Confirm:$false
$ErrorActionPreference = 'Stop'
$Admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Admin) { throw 'Run this in an Administrator PowerShell.' }
$Repo = Split-Path -Parent $PSScriptRoot
$Dest = 'D:\histold\admin-runner'
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name

New-Item -ItemType Directory -Force -Path $Dest, "$Dest\actions", "$Dest\requests", "$Dest\results" | Out-Null
# Lock the folder first, then copy: Administrators and SYSTEM may change it; you may only read it.
icacls $Dest /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "${User}:(OI)(CI)RX" | Out-Null
icacls $Dest /setowner '*S-1-5-32-544' /T /C | Out-Null
# The Telegram bot (running as you, not elevated) drops requests and reads results.
icacls "$Dest\requests" /grant "${User}:(OI)(CI)M" | Out-Null
Get-ChildItem "$Dest\actions" -Filter *.ps1 | Remove-Item -Force
Copy-Item -LiteralPath (Join-Path $Repo 'admin\runner.ps1') -Destination $Dest -Force
Copy-Item -Path (Join-Path $Repo 'admin\actions\*.ps1') -Destination "$Dest\actions" -Force

$Action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + "$Dest\runner.ps1" + '"')
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
# S4U: runs whether or not you are signed in, without storing a password, with no visible window.
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'Histold Admin Runner' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings `
    -Description 'Runs only allowlisted admin scripts from D:\histold\admin-runner (Administrators-only). Requests come from the Telegram bot after your approval.' -Force | Out-Null
# Let you (not just Administrators) start it on demand, so requests run within seconds instead of a minute.
try {
    $Service = New-Object -ComObject 'Schedule.Service'; $Service.Connect()
    $Task = $Service.GetFolder('\').GetTask('Histold Admin Runner')
    $Sid = (New-Object Security.Principal.NTAccount($User)).Translate([Security.Principal.SecurityIdentifier]).Value
    $Sddl = $Task.GetSecurityDescriptor(4)   # DACL only, so the appended ACE lands in the DACL
    if ($Sddl -notmatch [regex]::Escape($Sid)) { $Task.SetSecurityDescriptor($Sddl + "(A;;GRGX;;;$Sid)", 0) }
} catch { Write-Warning 'Could not allow on-demand starts; the runner still checks for requests every minute.' }

# Self-test: an elevated ping through the real request path.
$Id = [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText("$Dest\requests\$Id.json", (@{ id = $Id; action = 'ping' } | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Start-ScheduledTask -TaskName 'Histold Admin Runner'
for ($i = 0; $i -lt 30 -and -not (Test-Path "$Dest\results\$Id.json"); $i++) { Start-Sleep -Seconds 2 }
if (Test-Path "$Dest\results\$Id.json") { Write-Output ('Self-test: ' + (Get-Content "$Dest\results\$Id.json" -Raw | ConvertFrom-Json).output) }
else { Write-Warning 'Self-test did not finish within a minute; check D:\histold\admin-runner\runner.log' }
Write-Output "Installed. Allowlisted actions: $((Get-ChildItem "$Dest\actions" -Filter *.ps1).BaseName -join ', ')"
