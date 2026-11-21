# Rebuilds after you pull a new CameraScope version. Database migrations run automatically.
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Docker
Invoke-Compose @('up', '-d', '--build')
Wait-Healthy | Out-Null
Write-Host 'CameraScope updated.' -ForegroundColor Green
