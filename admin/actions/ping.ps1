# Read-only check that the runner works and is elevated.
$Elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"Admin runner OK as $([Security.Principal.WindowsIdentity]::GetCurrent().Name); elevated: $Elevated"
