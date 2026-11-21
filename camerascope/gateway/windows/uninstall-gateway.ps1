# Removes the CameraScope gateway (run as Administrator). Also delete the gateway in CameraScope > Manage > Gateways.
param([string]$InstallDir = "$env:ProgramData\CameraScopeGateway")
$ErrorActionPreference = 'Continue'
Stop-ScheduledTask -TaskName 'CameraScope Gateway' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'CameraScope Gateway' -Confirm:$false -ErrorAction SilentlyContinue
Get-Process mediamtx -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$InstallDir*" } | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*camerascope_gateway.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
Write-Host 'CameraScope gateway removed.'
