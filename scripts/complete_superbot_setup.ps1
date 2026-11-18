param([string]$Distribution='Ubuntu-24.04')
$ErrorActionPreference='Stop'
$Repo=Split-Path -Parent $PSScriptRoot
$Private='D:\histold\bot-workspace'
New-Item -ItemType Directory -Path $Private -Force | Out-Null
$ConfigPath=Join-Path $Private 'superbot.json'
if (Test-Path $ConfigPath) { $Settings=Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json }
else {
    $Bytes=New-Object byte[] 32
    $Rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Rng.GetBytes($Bytes) } finally { $Rng.Dispose() }
    $Settings=@{base_url='http://127.0.0.1:3007';password=[Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+','-').Replace('/','_');restricted_adapter_verified=$false}
    $Settings | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding utf8
}
$WslOutput=& wsl.exe --list --quiet 2>&1
$Installed=@($WslOutput | ForEach-Object { ([string]$_ -replace "`0",'').Trim() })
if ($Installed -notcontains $Distribution) {
    & wsl.exe --install --distribution $Distribution --no-launch
    if ($LASTEXITCODE -ne 0) { throw 'WSL installation is not ready. Complete the Windows restart first, then run this script again.' }
}
$env:SUPERBOT_AUTH_PASSWORD=$Settings.password
$WslConfig=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.wslconfig'
if (-not (Test-Path $WslConfig)) {
    "[wsl2]`nmemory=4GB`nprocessors=4`nswap=2GB`n" | Set-Content -LiteralPath $WslConfig -Encoding ascii
}
$OldWslEnv=[string]$env:WSLENV
$env:WSLENV=($OldWslEnv.TrimEnd(':')+':SUPERBOT_AUTH_PASSWORD').TrimStart(':')
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
try {
    $Bootstrap=(Get-Content -LiteralPath (Join-Path $Repo 'integrations/superbot/install-linux.sh') -Raw).Replace("`r`n","`n")
    $PatchBytes=[IO.File]::ReadAllBytes((Join-Path $Repo 'integrations/superbot/codex-read-only.patch'))
    $Bootstrap=$Bootstrap.Replace('__PATCH_B64__',[Convert]::ToBase64String($PatchBytes))
    $Bootstrap | & wsl.exe --distribution $Distribution --user root -- bash -s
    if ($LASTEXITCODE -ne 0) { throw 'Linux setup did not complete. Inspect the output; the runtime remains disconnected.' }
    $Settings.restricted_adapter_verified=$true
    $Settings | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding utf8
} finally {
    Remove-Item Env:SUPERBOT_AUTH_PASSWORD -ErrorAction SilentlyContinue
    $env:WSLENV=$OldWslEnv
}
Write-Output 'Build complete. Sign in to Codex inside the new Linux account:'
Write-Output 'wsl -d Ubuntu-24.04 -u histold-bots -- /home/histold-bots/.local/bin/codex login --device-auth'
Write-Output 'Then run scripts/start_superbot.ps1. Dashboard Superbot assignments will become available.'
