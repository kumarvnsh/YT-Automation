# Run in Administrator PowerShell after the WSL feature-enabling restart.
$ErrorActionPreference='Stop'
$Admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Admin) { throw 'Open PowerShell as Administrator to install the Microsoft WSL runtime package.' }
$Installer='D:\histold\tools\wsl.3.0.5.0.x64.msi'
if (-not (Test-Path $Installer)) { throw 'The downloaded WSL installer is missing.' }
$Signature=Get-AuthenticodeSignature -LiteralPath $Installer
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'The installer Microsoft signature could not be verified.' }
$Install=Start-Process -FilePath msiexec.exe -ArgumentList @('/i',('"'+$Installer+'"'),'/qn','/norestart','/l*v','D:\histold\wsl-install.log') -WindowStyle Hidden -Wait -PassThru
if ($Install.ExitCode -notin @(0,3010)) { throw "WSL installation failed (exit $($Install.ExitCode)); inspect D:\histold\wsl-install.log." }
Write-Output 'The Microsoft WSL runtime package is installed.'
if ($Install.ExitCode -eq 3010) { Write-Output 'Windows reports another restart is required. Save work before restarting.' }
else { & wsl.exe --version; Write-Output 'Ready for the Ubuntu/Superbot setup step in your normal Windows account.' }
# Retain the task's stored credentials; service cleanup is independent of WSL.
& (Join-Path $PSScriptRoot 'repair_histold_services.ps1')
