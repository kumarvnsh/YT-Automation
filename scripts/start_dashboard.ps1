# Start/restart only our dashboard when its code changes. Used at login and hourly.
param(
    [string]$DataRoot = 'D:\histold\YT-Automation',
    [int]$Port = 8787
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$HomeDir = Split-Path -Parent $DataRoot
$Py = Join-Path $DataRoot '.venv\Scripts\python.exe'
$Script = Join-Path $Repo 'scripts\dashboard_server.py'
$MetaPath = Join-Path $HomeDir 'dashboard_server.json'
$CodeFiles = @('scripts/dashboard_server.py','scripts/build_dashboard.py','scripts/clipping_dashboard.py','scripts/shotsnip_page.html','scripts/weekly_review.py',
    'scripts/content_insights.py','scripts/refresh_private_earnings.py','scripts/operations_tick.py','scripts/export_audience.py','src/operations.py','src/recovery.py','src/whatsapp_updates.py','src/insights.py','src/facebook_monetization.py','src/control.py','src/strategy.py','src/growth.py')
$Digest = ($CodeFiles | ForEach-Object { (Get-FileHash (Join-Path $Repo $_) -Algorithm SHA256).Hash }) -join ''
$Prior = $null
if (Test-Path $MetaPath) { try { $Prior = Get-Content $MetaPath -Raw | ConvertFrom-Json } catch {} }
if ($Prior) {
    $Proc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$Prior.pid)" -ErrorAction SilentlyContinue
    if (-not $Proc) {
        # A venv launcher can exit while its Python child keeps serving the port.
        $Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($Listener) {
            $Candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener[0].OwningProcess)" -ErrorAction SilentlyContinue
            if ($Candidate.ParentProcessId -eq [int]$Prior.pid) { $Proc = $Candidate }
        }
    }
    $ManagedDashboard = $Proc -and $Proc.CommandLine -and $Proc.CommandLine.Contains('dashboard_server.py')
    # Some scheduled-task processes hide CommandLine from a normal shell. Only
    # recognize one through BOTH the recorded parent/child PID and authenticated
    # dashboard response; never kill an unrelated listener just because of its port.
    if (-not $ManagedDashboard) {
        $KnownListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($KnownListener) {
            $KnownProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($KnownListener[0].OwningProcess)" -ErrorAction SilentlyContinue
            if ($KnownProcess -and ($KnownProcess.ProcessId -eq [int]$Prior.pid -or $KnownProcess.ParentProcessId -eq [int]$Prior.pid)) {
                try {
                    $LocalKey = (Get-Content -LiteralPath (Join-Path $HomeDir 'dashboard_key.txt') -Raw).Trim()
                    $Proof = Invoke-WebRequest "http://127.0.0.1:$Port/api/data" -Headers @{ Cookie = "hk=$LocalKey" } -UseBasicParsing -TimeoutSec 5
                    $ProofData = $Proof.Content | ConvertFrom-Json
                    if ($Proof.StatusCode -eq 200 -and $null -ne $ProofData.videos -and $ProofData.built) {
                        $ManagedDashboard = $true
                        $Proc = $KnownProcess
                    }
                } catch {}
            }
        }
    }
    if ($ManagedDashboard) {
        if ($Prior.digest -eq $Digest -and $Prior.repo -eq $Repo -and $Prior.port -eq $Port) {
            try {
                $Response = Invoke-WebRequest "http://127.0.0.1:$Port/login" -UseBasicParsing -TimeoutSec 3
                if ($Response.StatusCode -eq 200) { Write-Output "dashboard: running at http://localhost:$Port"; exit 0 }
            } catch {}
        }
        # Windows venv launchers spawn a second Python process; end both together.
        & taskkill.exe /PID $Proc.ProcessId /T /F | Out-Null
        Start-Sleep -Seconds 1
    }
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied by an unmanaged process. Leave it running and inspect it."
}
$env:PYTHONUTF8 = '1'
$env:OLLAMA_EXE = 'D:\Ollama\app\ollama.exe'
$env:OLLAMA_MODELS = 'D:\Ollama\models'
$Arguments = @('"' + $Script + '"', '--port', "$Port", '--data-root', '"' + $DataRoot + '"',
    '--key-file', '"' + (Join-Path $HomeDir 'dashboard_key.txt') + '"',
    '--log', '"' + (Join-Path $HomeDir 'strategist.log') + '"')
$Child = Start-Process -FilePath $Py -ArgumentList $Arguments -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $HomeDir 'dashboard-server.log') `
    -RedirectStandardError (Join-Path $HomeDir 'dashboard-server.err')
@{pid=$Child.Id; digest=$Digest; repo=$Repo; port=$Port} | ConvertTo-Json | Set-Content $MetaPath -Encoding UTF8
for ($Attempt=0; $Attempt -lt 15; $Attempt++) {
    Start-Sleep -Seconds 1
    try {
        if ((Invoke-WebRequest "http://127.0.0.1:$Port/login" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) {
            Write-Output "dashboard: started at http://localhost:$Port"; exit 0
        }
    } catch {}
}
throw 'Dashboard did not start; inspect dashboard-server.err'
