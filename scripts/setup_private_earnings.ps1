# Configure local read-only earnings access without changing publishing credentials.
param(
    [Parameter(Mandatory=$true)][ValidateSet('Meta','YouTube')][string]$Platform,
    [string]$DataRoot = 'D:\histold\YT-Automation'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $DataRoot '.venv\Scripts\python.exe'
if ($Platform -eq 'Meta') {
    $SecretDir = Join-Path (Split-Path -Parent $DataRoot) 'secrets'
    New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
    $TokenPath = Join-Path $SecretDir 'meta-earnings-token.txt'
    $Token = Read-Host -AsSecureString 'Meta token (hidden input; never paste into chat)'
    $Plain = [Net.NetworkCredential]::new('', $Token).Password
    $Plain = $Plain.Trim()
    if ([string]::IsNullOrWhiteSpace($Plain)) { throw 'Token was empty; nothing changed.' }
    if ($Plain.Length -lt 40 -or $Plain -match '\s' -or $Plain -match '[^\x21-\x7E]') {
        $Plain = $null; $Token.Dispose()
        throw 'Input is not a complete access token. Copy the full token from Meta, then paste it at this hidden prompt. Nothing was changed.'
    }
    # Create a restricted file before writing the credential.
    if (-not (Test-Path -LiteralPath $TokenPath)) { New-Item -ItemType File -Path $TokenPath | Out-Null }
    $Acl = [Security.AccessControl.FileSecurity]::new()
    $Acl.SetAccessRuleProtection($true, $false)
    $Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Sid, 'FullControl', 'Allow'))
    $Acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'), 'FullControl', 'Allow'))
    Set-Acl -LiteralPath $TokenPath -AclObject $Acl
    [IO.File]::WriteAllText($TokenPath, $Plain, [Text.UTF8Encoding]::new($false))
    $Plain = $null; $Token.Dispose()
}
$Arguments = @((Join-Path $Repo 'scripts\refresh_private_earnings.py'), '--config',
               (Join-Path $Repo 'config.yaml'), '--data-root', $DataRoot)
if ($Platform -eq 'YouTube') { $Arguments += '--authorize-youtube' }
& $Py @Arguments
exit $LASTEXITCODE
