# Apply the laptop's memory policy and restart only an idle, verified Ollama.
$ErrorActionPreference='Stop'
$Settings=@{
    OLLAMA_KEEP_ALIVE='60s'
    OLLAMA_MAX_LOADED_MODELS='1'
    OLLAMA_NUM_PARALLEL='1'
    OLLAMA_CONTEXT_LENGTH='4096'
    OLLAMA_MAX_QUEUE='4'
    OLLAMA_MODELS='D:\Ollama\models'
}
foreach ($Entry in $Settings.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($Entry.Key,$Entry.Value,'User')
    Set-Item -Path ('Env:'+ $Entry.Key) -Value $Entry.Value
}
$OllamaExe='D:\Ollama\app\ollama.exe'
if (-not (Test-Path -LiteralPath $OllamaExe)) {throw 'Installed Ollama executable was not found'}
$Busy=Get-NetTCPConnection -LocalPort 11434 -State Established -ErrorAction SilentlyContinue
if ($Busy) {throw 'Policy saved. An Ollama request is active; restart when its inference finishes.'}
foreach ($ProcessName in @('ollama app.exe','ollama.exe')) {
$Processes=Get-CimInstance Win32_Process | Where-Object {$_.Name -eq $ProcessName}
foreach ($Entry in $Processes) {
    if ($Entry.ExecutablePath -and $Entry.ExecutablePath.StartsWith('D:\Ollama\app\',[StringComparison]::OrdinalIgnoreCase)) {
        # Only this verified model server and its own runner children.
        & taskkill.exe /PID $Entry.ProcessId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) {throw 'Could not stop the verified Ollama process'}
    }
}
}
$LogDirectory='D:\histold'
Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDirectory 'ollama-memory-policy.log') `
    -RedirectStandardError (Join-Path $LogDirectory 'ollama-memory-policy.err') | Out-Null
for ($Attempt=0;$Attempt -lt 20;$Attempt++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
        Write-Output 'Ollama restarted: one model, one inference, 60-second idle unload, 4K default context.'
        exit 0
    } catch {}
}
throw 'Ollama did not respond after restart; inspect its private memory-policy log'
