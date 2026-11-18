$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
$Private='D:\histold\bot-workspace'
try {
    $Health=Invoke-RestMethod 'http://127.0.0.1:3007/healthz' -TimeoutSec 3
    if ($Health.ok) { Write-Output 'Superbot is already running.'; exit 0 }
} catch {}
$Config=Get-Content -LiteralPath (Join-Path $Private 'superbot.json') -Raw | ConvertFrom-Json
if (-not $Config.restricted_adapter_verified) { throw 'Complete the restricted runtime installation first.' }
& wsl.exe -d Ubuntu-24.04 -u histold-bots -- /home/histold-bots/.local/bin/codex login status
if ($LASTEXITCODE -ne 0) { throw 'Sign in to Codex inside Linux before starting Superbot.' }
Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d','Ubuntu-24.04','-u','histold-bots','--','/home/histold-bots/start-superbot.sh') -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Private 'runtime.log') -RedirectStandardError (Join-Path $Private 'runtime.err') | Out-Null
Write-Output 'Superbot start requested. The Bot workspace checks its connection automatically.'
