# Runs MediaMTX and the CameraScope agent; restarts either if it exits. Started by the
# "CameraScope Gateway" Scheduled Task. Logs: gateway.log / mediamtx.log in this folder.
$ErrorActionPreference = 'Continue'
$dir = $PSScriptRoot
Get-Content "$dir\gateway.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process') }
}
# After the first successful enrollment the secret lives in state\gateway.json; drop the one-time code.
if (Test-Path "$dir\state\gateway.json") { [Environment]::SetEnvironmentVariable('ENROLLMENT_CODE', $null, 'Process') }

$mtx = $null; $agent = $null
while ($true) {
    if (-not $mtx -or $mtx.HasExited) {
        $mtx = Start-Process -FilePath "$dir\mediamtx\mediamtx.exe" -ArgumentList "`"$dir\mediamtx\mediamtx.yml`"" -WorkingDirectory "$dir\mediamtx" `
            -RedirectStandardOutput "$dir\mediamtx.log" -RedirectStandardError "$dir\mediamtx.err.log" -PassThru
    }
    if (-not $agent -or $agent.HasExited) {
        if (Test-Path "$dir\state\gateway.json") { [Environment]::SetEnvironmentVariable('ENROLLMENT_CODE', $null, 'Process') }
        $agent = Start-Process -FilePath "$dir\venv\Scripts\python.exe" -ArgumentList "`"$dir\agent\camerascope_gateway.py`"" -WorkingDirectory "$dir\agent" `
            -RedirectStandardOutput "$dir\gateway.out.log" -RedirectStandardError "$dir\gateway.log" -PassThru
    }
    Start-Sleep -Seconds 10
}
