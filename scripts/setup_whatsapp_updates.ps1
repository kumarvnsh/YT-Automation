# Enable daily owner-only updates after the OpenWA session is paired.
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^\+?[0-9]{7,15}$')][string]$Recipient,
    [string]$SessionId,
    [string]$DataRoot = 'D:\histold\YT-Automation'
)
$ErrorActionPreference = 'Stop'
$Secrets = Join-Path (Split-Path -Parent $DataRoot) 'secrets'
$KeyPath = Join-Path $Secrets 'openwa-api-key.txt'
if (-not (Test-Path $KeyPath)) { throw 'OpenWA API key file is missing. Finish gateway setup first.' }
$Key = (Get-Content $KeyPath -Raw).Trim()
if (-not $SessionId) {
    $SessionId = (Get-Content (Join-Path (Split-Path -Parent $DataRoot) 'openwa-session.json') -Raw | ConvertFrom-Json).id
}
$Base = 'http://127.0.0.1:2785'
$Session = Invoke-RestMethod "$Base/api/sessions/$SessionId" -Headers @{'X-API-Key'=$Key}
if ($Session.status -ne 'ready') {
    throw 'Session is not connected. Scan the QR code in the OpenWA dashboard first.'
}
$Existing = Get-Content (Join-Path $Secrets 'whatsapp-updates.json') -Raw | ConvertFrom-Json
if ($Existing.session_id -ne $SessionId -or $Existing.recipient -ne $Recipient.TrimStart('+') -or -not $Existing.api_key) {
    throw 'Create a key restricted to this sender session and recipient before enabling delivery.'
}
$Config = @{enabled=$true; base_url=$Base; session_id=$SessionId; recipient=$Recipient.TrimStart('+'); api_key=$Existing.api_key;
            send_alerts=$true; daily_time='09:00'; timezone='Asia/Kolkata'}
[IO.File]::WriteAllText((Join-Path $Secrets 'whatsapp-updates.json'), ($Config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Output 'Owner-only WhatsApp briefings enabled for 9:00 AM IST; failures will also trigger alerts. No test message was sent.'
