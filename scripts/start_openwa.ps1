param([string]$HomeDir = 'D:\histold')
$ErrorActionPreference = 'Stop'
if (Get-NetTCPConnection -LocalPort 2785 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
$Node = Join-Path $HomeDir 'tools\node-v24.22.3-win-x64\node.exe'
$Gateway = Join-Path $HomeDir 'OpenWA'
if (-not (Test-Path (Join-Path $Gateway 'dist\main.js'))) { throw 'Build OpenWA before starting the gateway.' }
$Process = Start-Process -FilePath $Node -ArgumentList 'dist/main.js' -WorkingDirectory $Gateway -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $HomeDir 'openwa-runtime.log') -RedirectStandardError (Join-Path $HomeDir 'openwa-runtime.err')
@{pid=$Process.Id;root=$Gateway} | ConvertTo-Json | Set-Content (Join-Path $HomeDir 'openwa-process.json')
Write-Output 'OpenWA started on localhost; waiting for its health check.'
