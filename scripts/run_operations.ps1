param([string]$DataRoot = 'D:\histold\YT-Automation')
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$HomeDir = Split-Path -Parent $DataRoot
$env:PYTHONUTF8='1'
# Service recovery is handled and reported by operations_tick; a failed gateway
# must not prevent analytics monitoring from running.
& (Join-Path $DataRoot '.venv\Scripts\python.exe') (Join-Path $Repo 'scripts\operations_tick.py') --data-root $DataRoot `
    >> (Join-Path $HomeDir 'operations.log') 2>&1
exit $LASTEXITCODE
