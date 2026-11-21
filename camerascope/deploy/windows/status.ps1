# Shows container state, health and how the URL is published.
. (Join-Path $PSScriptRoot 'common.ps1')
Invoke-Compose @('ps')
try { $h = Invoke-RestMethod http://127.0.0.1:8080/api/health -TimeoutSec 5; Write-Host "health: $($h.status), streaming gateway: $($h.gateway)" }
catch { Write-Warn 'web entry point (127.0.0.1:8080) not answering' }
& tailscale serve status
