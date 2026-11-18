# One-time Telegram setup (normal PowerShell, no admin needed):
#   1. In Telegram, talk to @BotFather, send /newbot, and copy the token it gives you.
#   2. Run this script and paste the token when asked (it is not echoed or logged).
#   3. Send the /pair code it prints to your new bot. Only that Telegram account is obeyed.
# Re-run with -PairOnly to move control to a different Telegram account.
param([switch]$PairOnly)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Home_ = 'D:\histold'
$Secrets = Join-Path $Home_ 'secrets'
$ConfigPath = Join-Path $Secrets 'telegram.json'
$Py = 'D:\histold\YT-Automation\.venv\Scripts\python.exe'
$PyW = 'D:\histold\YT-Automation\.venv\Scripts\pythonw.exe'
New-Item -ItemType Directory -Force -Path $Secrets, (Join-Path $Home_ 'control') | Out-Null

if (-not $PairOnly) {
    $Secure = Read-Host 'Paste the bot token from @BotFather' -AsSecureString
    $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)).Trim()
    if ($Token -notmatch '^\d{5,15}:[A-Za-z0-9_-]{30,64}$') { throw 'That does not look like a BotFather token.' }
    $Me = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$Token/getMe" -TimeoutSec 20
    if (-not $Me.ok) { throw 'Telegram rejected the token.' }
    $Config = @{ token = $Token; bot_username = $Me.result.username }
    [IO.File]::WriteAllText($ConfigPath, ($Config | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    # Only you and the system can read the token.
    $User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    icacls $ConfigPath /inheritance:r /grant:r "${User}:(R,W)" 'SYSTEM:(F)' 'Administrators:(F)' | Out-Null
    Write-Output "Token saved for @$($Me.result.username)."

    $Action = New-ScheduledTaskAction -Execute $PyW -Argument ('"' + (Join-Path $Repo 'scripts\telegram_bot.py') + '"') -WorkingDirectory $Repo
    $Triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $User),
                  (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)))
    $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName 'Histold Telegram' -Action $Action -Trigger $Triggers -Principal $Principal -Settings $Settings `
        -Description 'Telegram updates and approvals for Histold. Obeys only the paired account. Log: D:\histold\control\telegram-bot.log' -Force | Out-Null
    Start-ScheduledTask -TaskName 'Histold Telegram'
    Write-Output 'Task "Histold Telegram" installed and started (restarts within 5 minutes if it stops).'
}
& $Py (Join-Path $Repo 'scripts\telegram_bot.py') --pair
