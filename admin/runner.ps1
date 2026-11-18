# Histold admin runner. Runs elevated from the "Histold Admin Runner" task (every minute and on demand).
# It executes ONLY the allowlisted scripts below, from its own Administrators-only folder, and never
# runs text from a request. Requests are JSON files naming one allowlisted action; there are no
# arguments. Installed and updated by scripts\install_admin_runner.ps1 (run as Administrator).
param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Requests = Join-Path $Root 'requests'
$Results = Join-Path $Root 'results'
$Log = Join-Path $Root 'runner.log'
$Allowed = @{
    'ping'                    = 'ping.ps1'
    'stop-elevated-dashboard' = 'stop-elevated-dashboard.ps1'
    'stop-stale-monitors'     = 'stop-stale-monitors.ps1'
    'restart-tailscale'       = 'restart-tailscale.ps1'
}
function Write-Log([string]$Text) { Add-Content -LiteralPath $Log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Text) -Encoding utf8 }
function Write-Result([string]$Id, [string]$Status, [string]$Output) {
    $Body = @{ id = $Id; status = $Status; output = $Output; finished = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json
    $Temp = Join-Path $Results ($Id + '.tmp')
    [IO.File]::WriteAllText($Temp, $Body, (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $Temp -Destination (Join-Path $Results ($Id + '.json')) -Force
}

$Mutex = New-Object Threading.Mutex($false, 'Global\HistoldAdminRunner')
if (-not $Mutex.WaitOne(0)) { exit 0 }
try {
    New-Item -ItemType Directory -Force -Path $Requests, $Results | Out-Null
    # Old results are only needed until the bot reads them.
    Get-ChildItem -LiteralPath $Results -File | Where-Object LastWriteTime -lt (Get-Date).AddDays(-2) | Remove-Item -Force
    foreach ($File in @(Get-ChildItem -LiteralPath $Requests -Filter '*.json' -File | Sort-Object LastWriteTime | Select-Object -First 5)) {
        $Id = $File.BaseName
        $Raw = $null
        try { $Raw = Get-Content -LiteralPath $File.FullName -Raw -ErrorAction Stop } catch {}
        Remove-Item -LiteralPath $File.FullName -Force   # one attempt per request, never a replay
        if ($Id -notmatch '^[a-f0-9]{32}$') { Write-Log "rejected malformed request name $($File.Name)"; continue }
        try { $Request = $Raw | ConvertFrom-Json } catch { Write-Result $Id 'rejected' 'Request is not valid JSON.'; continue }
        $Action = [string]$Request.action
        if (-not $Allowed.ContainsKey($Action)) { Write-Log "rejected $Id action '$Action'"; Write-Result $Id 'rejected' 'Action is not allowlisted.'; continue }
        if ($File.LastWriteTime -lt (Get-Date).AddMinutes(-15)) { Write-Result $Id 'rejected' 'Request expired (older than 15 minutes).'; continue }
        Write-Log "running $Id $Action"
        try {
            $Output = & (Join-Path $Root ('actions\' + $Allowed[$Action])) 2>&1 | Out-String
            Write-Result $Id 'ok' $Output.Trim()
            Write-Log "finished $Id $Action ok"
        } catch {
            Write-Result $Id 'failed' ($_.Exception.Message)
            Write-Log "finished $Id $Action failed: $($_.Exception.Message)"
        }
    }
} finally { $Mutex.ReleaseMutex() }
