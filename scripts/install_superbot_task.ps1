$ErrorActionPreference='Stop'
$User=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$Script=Join-Path $PSScriptRoot 'start_superbot.ps1'
$Action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$Script+'"')
$Trigger=New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Histold Superbot Runtime' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
& $Script
