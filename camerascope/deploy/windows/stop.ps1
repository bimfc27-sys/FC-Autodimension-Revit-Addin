# Stops CameraScope and removes the Tailscale HTTPS publication. Data is kept.
. (Join-Path $PSScriptRoot 'common.ps1')
& tailscale funnel reset *> $null
& tailscale serve reset *> $null
Invoke-Compose @('down')
Write-Host 'CameraScope stopped (data kept in the Docker volume "camerascope_pgdata").'
