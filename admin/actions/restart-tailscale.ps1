# Restart the Tailscale service that gives the phone access to the dashboard.
Restart-Service -Name 'Tailscale' -Force
Start-Sleep -Seconds 5
"Tailscale service: $((Get-Service -Name 'Tailscale').Status)"
