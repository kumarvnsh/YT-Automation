param([string]$DataRoot = 'D:\histold\YT-Automation')
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$HomeDir = Split-Path -Parent $DataRoot
$env:PYTHONUTF8='1'
& (Join-Path $Repo 'scripts\start_openwa.ps1') -HomeDir $HomeDir
& (Join-Path $DataRoot '.venv\Scripts\python.exe') (Join-Path $Repo 'scripts\operations_tick.py') --data-root $DataRoot `
    >> (Join-Path $HomeDir 'operations.log') 2>&1
exit $LASTEXITCODE
