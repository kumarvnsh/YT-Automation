# Start the Shotsnip local clipping worker through the dashboard (same path as the
# "Start local clipping" button). Skips when the owner pressed Stop, unless -Force
# (used at login so a restart always resumes clipping). Never starts a duplicate:
# the worker holds work\dashboard\worker.lock while running.
param(
    [string]$ClipRoot = $(if ($env:SHOTSNIP_ROOT) { $env:SHOTSNIP_ROOT } else { 'D:\YT-Cliping-Automation' }),
    [string]$HomeDir = 'D:\histold',
    [int]$Port = 8787,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$Work = Join-Path $ClipRoot 'work\dashboard'
if (-not (Test-Path (Join-Path $ClipRoot 'watch_service.py'))) { Write-Output "shotsnip: no worker at $ClipRoot"; exit 0 }
if (-not $Force -and (Test-Path (Join-Path $Work 'stop-worker'))) { Write-Output 'shotsnip: paused by owner (Stop pressed); not starting'; exit 0 }

$Lock = Join-Path $Work 'worker.lock'
if (Test-Path $Lock) {
    try {
        $Stream = [System.IO.File]::Open($Lock, 'Open', 'ReadWrite', 'ReadWrite')
        try { $Stream.Lock(0, 1); $Stream.Unlock(0, 1) } finally { $Stream.Dispose() }
    } catch { Write-Output 'shotsnip: worker already running'; exit 0 }
}

$Key = (Get-Content -LiteralPath (Join-Path $HomeDir 'dashboard_key.txt') -Raw).Trim()
$Body = '{"name":"start_worker"}'
# Windows PowerShell 5.1 silently drops a Cookie passed via -Headers; use a session.
$Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$Session.Cookies.Add((New-Object System.Net.Cookie('hk', $Key, '/', '127.0.0.1')))
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    try {
        $Response = Invoke-WebRequest "http://127.0.0.1:$Port/api/shotsnip/action" -Method Post -Body $Body `
            -ContentType 'application/json' -Headers @{ 'X-Histold' = '1' } -WebSession $Session -UseBasicParsing -TimeoutSec 10
        Write-Output ('shotsnip: ' + ($Response.Content | ConvertFrom-Json).message)
        exit 0
    } catch { Start-Sleep -Seconds 3 }
}
throw 'shotsnip: dashboard did not accept the start request'
