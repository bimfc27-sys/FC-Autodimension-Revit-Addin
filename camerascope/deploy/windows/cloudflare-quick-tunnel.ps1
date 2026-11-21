<#
  OPTIONAL alternative to Tailscale Funnel: a Cloudflare Quick Tunnel (free, no account).
  LIMITATIONS (Cloudflare documents these as testing-only):
    * the https://<random>.trycloudflare.com URL CHANGES every time this runs,
    * 200 concurrent request limit, no uptime guarantee,
    * WebRTC media cannot pass: choose "HLS only" in Manage > Settings on the phone.
  Install: winget install -e --id Cloudflare.cloudflared
#>
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command cloudflared 'Install it with: winget install -e --id Cloudflare.cloudflared'
Write-Warn 'Leave this window open. The public URL is printed below (look for trycloudflare.com).'
& cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080
