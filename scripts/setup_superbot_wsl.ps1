# Run in Administrator PowerShell. Enables prerequisites without restarting Windows.
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Open PowerShell as Administrator, then run this script again.' }
foreach ($feature in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform')) {
    & dism.exe /online /enable-feature /featurename:$feature /all /norestart
    if ($LASTEXITCODE -notin @(0,3010)) { throw "Windows could not enable $feature (exit $LASTEXITCODE)." }
}
Write-Output 'WSL prerequisites enabled. Save your work, then restart Windows when the dashboard integration is ready.'
Write-Output 'After restart: wsl --install --distribution Ubuntu-24.04 --no-launch'
