param([string]$HomeDir = 'D:\histold')
$ErrorActionPreference = 'Stop'
# Restore the existing authenticated WhatsApp session after a gateway restart.
$env:AUTO_START_SESSIONS = 'true'
if (Get-NetTCPConnection -LocalPort 2785 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
$Node = Join-Path $HomeDir 'tools\node-v24.22.3-win-x64\node.exe'
$Gateway = Join-Path $HomeDir 'OpenWA'
if (-not (Test-Path (Join-Path $Gateway 'dist\main.js'))) { throw 'Build OpenWA before starting the gateway.' }
$StartLock=$null
try { $StartLock=[IO.File]::Open((Join-Path $HomeDir 'openwa-start.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
catch [IO.IOException] { Write-Output 'Another OpenWA startup check is running.'; exit 0 }
try {
if (Get-NetTCPConnection -LocalPort 2785 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
$Metadata=Join-Path $HomeDir 'openwa-process.json'
if (Test-Path $Metadata) {
    try {
        $Prior=Get-Content -LiteralPath $Metadata -Raw | ConvertFrom-Json
        $Existing=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$Prior.pid)" -ErrorAction SilentlyContinue
        $SavedAt=(Get-Item -LiteralPath $Metadata).LastWriteTime
        if ($Existing -and $Existing.Name -eq 'node.exe' -and $Prior.root -eq $Gateway -and [Math]::Abs(($Existing.CreationDate-$SavedAt).TotalSeconds) -lt 60) {
            Write-Output 'The recorded OpenWA process is still starting or needs inspection; leaving it running instead of creating a duplicate.'
            exit 0
        }
    } catch {}
}
$Process = Start-Process -FilePath $Node -ArgumentList 'dist/main.js' -WorkingDirectory $Gateway -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $HomeDir 'openwa-runtime.log') -RedirectStandardError (Join-Path $HomeDir 'openwa-runtime.err')
@{pid=$Process.Id;root=$Gateway} | ConvertTo-Json | Set-Content (Join-Path $HomeDir 'openwa-process.json')
Write-Output 'OpenWA started on localhost; waiting for its health check.'
} finally { if ($StartLock) { $StartLock.Dispose() } }
