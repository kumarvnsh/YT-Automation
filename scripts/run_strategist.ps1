# Windows laptop runner, hourly (Task Scheduler: "Histold Strategist"). Runs in a
# DEDICATED clone, so it hard-resets to origin/master each time. Each run: dispatches
# publish.yml (at growth-plan hours) and analytics.yml, refreshes data/strategy.json
# (every 4h), the growth plan (daily) and the weekly review, pushes only those files,
# and rebuilds the local dashboard.
param(
    [string]$Log = 'D:\histold\strategist.log',
    [string]$OllamaExe = 'D:\Ollama\app\ollama.exe',
    [string]$DataRoot = 'D:\histold\YT-Automation'
)

$ErrorActionPreference = 'Continue'
$Repo = (Resolve-Path -LiteralPath $DataRoot).Path
if ($Repo -eq (Split-Path -Parent $PSScriptRoot)) { throw 'The strategist must use its dedicated data clone, not the dashboard source checkout.' }
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
$Failed = @()

# Keep the dashboard available while logged off too, and restart it on code updates.
if ((Invoke-Logged powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'start_dashboard.ps1'), '-DataRoot', $Repo)) -ne 0) { $Failed += 'dashboard server' }

# 1. Dispatch first: it is the time-critical step (publish only in the hour's first 20 min).
if ((Invoke-Logged $Py @('scripts\dispatch.py')) -ne 0) { $Failed += 'dispatch' }

# 2. Topic strategy every 4 hours.
$StrategyAgeH = 99
try {
    $updated = (Get-Content 'data\strategy.json' -Raw | ConvertFrom-Json).updated
    $StrategyAgeH = ((Get-Date).ToUniversalTime() - ([datetimeoffset]"$updated").UtcDateTime).TotalHours
} catch {}
if ($StrategyAgeH -ge 3.9) {
    if ((Invoke-Logged $Py @('scripts\strategist.py')) -ne 0) { $Failed += 'strategist' }
}

# 3. Growth agent (once a day) and weekly review (once per ISO week, local LLM).
if (-not $env:OLLAMA_EXE -and (Test-Path $OllamaExe)) { $env:OLLAMA_EXE = $OllamaExe }
if ((Invoke-Logged $Py @('scripts\growth_agent.py', '--if-stale')) -ne 0) { $Failed += 'growth_agent' }
if ((Invoke-Logged $Py @('scripts\weekly_review.py', '--if-missing')) -ne 0) { $Failed += 'weekly_review' }
if ((Invoke-Logged $Py @('scripts\content_insights.py', '--if-stale')) -ne 0) { $Failed += 'content_insights' }

# 4. Commit and push. Only these outputs are ever committed from this laptop.
function Push-Outputs {
    $Outputs = @('data/strategy.json', 'data/growth_plan.json', 'data/reviews') | Where-Object { Test-Path $_ }
    Invoke-Logged git (@('add', '--') + $Outputs) | Out-Null
    & git diff --cached --quiet -- @Outputs
    if ($LASTEXITCODE -eq 0) { return 'nothing to push' }
    if ((Invoke-Logged git (@('commit', '-q', '-m', 'chore: refresh topic strategy and growth plan [skip ci]', '--') + $Outputs)) -ne 0) {
        return '!commit failed'
    }
    # Bots push to master hourly: on a rejected push, fetch, rebase and retry (up to 3 retries).
    for ($attempt = 0; $attempt -le 3; $attempt++) {
        if ((Invoke-Logged git @('push', '-q', 'origin', 'HEAD:master')) -eq 0) { return "pushed $(& git rev-parse --short HEAD)" }
        if ($attempt -eq 3) { break }
        Invoke-Logged git @('fetch', '-q', 'origin') | Out-Null
        if ((Invoke-Logged git @('rebase', '-q', 'origin/master')) -ne 0) {
            Invoke-Logged git @('rebase', '--abort') | Out-Null
            return '!rebase failed'
        }
    }
    return '!push failed after 3 retries'
}
$Pushed = Push-Outputs
if ($Pushed.StartsWith('!')) { $Failed += $Pushed.Substring(1) }

# 5. Rebuild the laptop dashboard (D:\histold\dashboard\index.html).
if ((Invoke-Logged $Py @('scripts\build_dashboard.py', '--log', $Log)) -ne 0) { $Failed += 'dashboard' }

if ($Failed) { Write-Log "!! failed: $($Failed -join ', ')"; Finish 1 "$Pushed; failed: $($Failed -join ', ')" }
Finish 0 $Pushed
