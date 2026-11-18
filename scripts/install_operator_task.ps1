# Installs "Histold Operator": every 2 hours while you are signed in to Windows (Claude Code and
# GitHub sign-ins come from your user profile). Stop it any time: create D:\histold\operator\PAUSE
# or run: Disable-ScheduledTask -TaskName 'Histold Operator'
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $Repo 'scripts\run_operator.ps1') + '"')
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Hours 2)
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 90)
Register-ScheduledTask -TaskName 'Histold Operator' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings `
    -Description 'Autonomous self-repair and content experiments every 3 days for Histold and Shotsnip. PAUSE file: D:\histold\operator\PAUSE' -Force | Out-Null
Write-Output 'Histold Operator installed: runs every 2 hours while you are signed in. Log: D:\histold\operator\operator.log'
