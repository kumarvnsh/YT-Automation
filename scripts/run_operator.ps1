# One Operator tick (self-repair + content experiment every 3 days). Logs to D:\histold\operator\operator.log.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$State = 'D:\histold\operator'
New-Item -ItemType Directory -Force -Path $State, 'D:\histold\tmp' | Out-Null
$env:PYTHONUTF8 = '1'; $env:TMP = 'D:\histold\tmp'; $env:TEMP = 'D:\histold\tmp'
$PyArgs = @((Join-Path $Repo 'scripts\operator_tick.py'))
if ($DryRun) { $PyArgs += '--dry-run' }
& 'D:\histold\YT-Automation\.venv\Scripts\python.exe' @PyArgs >> (Join-Path $State 'operator.log') 2>&1
exit $LASTEXITCODE
