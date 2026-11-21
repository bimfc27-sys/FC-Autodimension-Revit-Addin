# Starts CameraScope (after a reboot Docker Desktop restarts it automatically if Docker starts at login).
param([switch]$Public)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Docker
Invoke-Compose @('up', '-d')
Wait-Healthy | Out-Null
& tailscale serve --bg http://127.0.0.1:8080
if ($Public) { & tailscale funnel --bg http://127.0.0.1:8080 }
$ts = Get-TailscaleInfo
Write-Host "CameraScope: https://$($ts.DnsName)" -ForegroundColor Green
