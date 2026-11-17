# Run once in Administrator PowerShell. Does not change the publishing task.
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $Repo 'scripts\run_operations.ps1')+'"')
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddHours(9)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'Histold Jarvis Operations' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings `
    -Description 'Local health monitoring, private audience reporting and owner-only WhatsApp briefing at 9 AM IST.' -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName 'Histold Jarvis Operations' -ErrorAction Stop
Write-Output 'Jarvis operations task installed; runs logged on or off, checks every minute and catches missed starts.'
