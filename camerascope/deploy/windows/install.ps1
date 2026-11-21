<#
.SYNOPSIS
  Installs CameraScope on this Windows PC and publishes it with Tailscale (free).

.PARAMETER Public
  Also publish the URL to the whole internet with Tailscale Funnel, so the iPhone
  does not need the Tailscale app. Video then uses HLS (a few seconds of delay),
  because WebRTC media cannot pass through Funnel.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Public
#>
param([switch]$Public)
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Step 'Checking prerequisites'
Assert-Docker
$ts = Get-TailscaleInfo
Write-Host "    Tailscale name: $($ts.DnsName)  IP: $($ts.Ip)"

Write-Step 'Creating configuration (.env)'
$cfg = Read-EnvFile
if (-not $cfg['POSTGRES_PASSWORD']) { $cfg['POSTGRES_PASSWORD'] = New-Secret 24 -UrlSafe }
if (-not $cfg['JWT_SECRET'])        { $cfg['JWT_SECRET'] = New-Secret 48 -UrlSafe }
if (-not $cfg['CREDENTIAL_KEYS'])   { $cfg['CREDENTIAL_KEYS'] = "k$(Get-Date -Format yyyyMMdd):$(New-Secret 32)" }
if (-not $cfg['REGISTRATION_MODE']) { $cfg['REGISTRATION_MODE'] = 'first_user_only' }
$cfg['PUBLIC_HOST'] = $ts.Ip
$cfg['SITE_URL'] = "https://$($ts.DnsName)"
if (-not $cfg['STREAMING_MODE']) { $cfg['STREAMING_MODE'] = 'auto' }
Write-EnvFile $cfg
Write-Host '    .env written (secrets generated, file readable only by you). BACK IT UP: losing CREDENTIAL_KEYS makes stored camera passwords unrecoverable.'

Write-Step 'Building and starting CameraScope (first run downloads ~1 GB and takes several minutes)'
Invoke-Compose @('up', '-d', '--build')
$health = Wait-Healthy
Write-Host "    healthy (streaming gateway: $($health.gateway))"

Write-Step 'Windows Firewall: allow WebRTC video (port 8189) from your tailnet only'
if (Test-Admin) {
    Get-NetFirewallRule -DisplayName 'CameraScope WebRTC (Tailscale)*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'CameraScope WebRTC (Tailscale) UDP' -Direction Inbound -Protocol UDP -LocalPort 8189 -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
    New-NetFirewallRule -DisplayName 'CameraScope WebRTC (Tailscale) TCP' -Direction Inbound -Protocol TCP -LocalPort 8189 -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
    Write-Host '    firewall rules added'
} else {
    Write-Warn 'Not running as Administrator: firewall rule skipped. Re-run in an elevated PowerShell for low-latency WebRTC video, otherwise video falls back to HLS.'
}

Write-Step "Publishing https://$($ts.DnsName)"
# Tailscale Serve: HTTPS with a valid certificate, reachable from devices in YOUR tailnet.
& tailscale serve --bg http://127.0.0.1:8080
if ($LASTEXITCODE -ne 0) { throw 'tailscale serve failed. If it printed a link, open it to enable HTTPS certificates for your tailnet, then run install.ps1 again.' }
if ($Public) {
    # Tailscale Funnel: same URL, reachable from the whole internet (no app needed on the phone).
    & tailscale funnel --bg http://127.0.0.1:8080
    if ($LASTEXITCODE -ne 0) { throw 'tailscale funnel failed. If it printed a link, open it to allow Funnel for this device, then run install.ps1 -Public again.' }
}

Write-Host ''
Write-Host "CameraScope is running:  https://$($ts.DnsName)" -ForegroundColor Green
if ($Public) {
    Write-Host '  Public (Funnel): any browser can open it. In CameraScope > Manage > Settings, choose "HLS only" on phones that do not run Tailscale.'
} else {
    Write-Host '  Private (Serve): install the free Tailscale app on your iPhone, sign in with the same account, then open the URL in Safari.'
}
Write-Host '  First visit: register your account - it becomes the administrator; further self-registration is closed.'
Write-Host '  Keep this PC awake:   powercfg /change standby-timeout-ac 0'
