# Writes a database backup next to this script (store it together with a copy of .env).
. (Join-Path $PSScriptRoot 'common.ps1')
$file = Join-Path $PSScriptRoot ("backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sql')
& docker @Compose exec -T postgres pg_dump -U camerascope camerascope | Out-File -Encoding utf8 $file
Write-Host "backup written: $file"
