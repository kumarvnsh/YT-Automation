# Windows laptop-side topic strategist (Task Scheduler: "Histold Strategist").
# Windows equivalent of run_strategist.sh. Runs in a DEDICATED clone, so it
# hard-resets to origin/master each time, refreshes data/strategy.json and
# pushes only that file for the publish runs to read.
param(
    [string]$Log = 'D:\histold\strategist.log'
)

$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:GIT_TERMINAL_PROMPT = '0'   # never hang waiting for credentials
$env:GCM_INTERACTIVE = 'never'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Write-Log([string[]]$Lines) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $Lines | ForEach-Object { "$ts  $_" } | Add-Content -Path $Log -Encoding utf8
}

# Run a native command, append its output (stdout+stderr) to the log, return its exit code.
function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
    Write-Log "> $Exe $($Arguments -join ' ')"
    $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    if ($out) { Write-Log $out }
    return $code
}

function Finish([int]$Code, [string]$Msg) {
    Write-Log "== $Msg (exit $Code)"
    exit $Code
}

Set-Location $Repo
Write-Log '== strategist run start'

if ((Invoke-Logged git @('fetch', '-q', 'origin')) -ne 0) { Finish 1 'fetch failed' }
if ((Invoke-Logged git @('reset', '-q', '--hard', 'origin/master')) -ne 0) { Finish 1 'reset failed' }
if ((Invoke-Logged $Py @('scripts\strategist.py')) -ne 0) { Finish 1 'strategist.py failed' }

Invoke-Logged git @('add', 'data/strategy.json') | Out-Null
& git diff --cached --quiet -- data/strategy.json
if ($LASTEXITCODE -eq 0) { Finish 0 'strategy unchanged' }

if ((Invoke-Logged git @('commit', '-q', '-m', 'chore: refresh topic strategy [skip ci]', '--', 'data/strategy.json')) -ne 0) {
    Finish 1 'commit failed'
}

# Bots push to master hourly: on a rejected push, fetch, rebase and retry (up to 3 retries).
for ($attempt = 0; $attempt -le 3; $attempt++) {
    if ((Invoke-Logged git @('push', '-q', 'origin', 'HEAD:master')) -eq 0) {
        $sha = (& git rev-parse --short HEAD)
        Finish 0 "pushed $sha"
    }
    if ($attempt -eq 3) { break }
    Invoke-Logged git @('fetch', '-q', 'origin') | Out-Null
    if ((Invoke-Logged git @('rebase', '-q', 'origin/master')) -ne 0) {
        Invoke-Logged git @('rebase', '--abort') | Out-Null
        Finish 1 'rebase failed'
    }
}
Finish 1 'push failed after 3 retries'
