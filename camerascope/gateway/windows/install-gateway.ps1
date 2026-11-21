<#
.SYNOPSIS
  Installs the CameraScope Local Camera Gateway natively on an EXISTING Windows PC
  (no Docker, no extra hardware). Run in an elevated (Administrator) PowerShell.

.DESCRIPTION
  * Installs into C:\ProgramData\CameraScopeGateway (state/secrets readable only by
    SYSTEM and Administrators).
  * Downloads the official MediaMTX v1.9.3 Windows build from GitHub and verifies its SHA-256
    against the release's published checksums.
  * Creates a Python virtual environment for the agent (needs Python 3.10+:
    winget install -e --id Python.Python.3.12).
  * Registers a Scheduled Task "CameraScope Gateway" that starts at boot as SYSTEM and
    restarts the gateway if it stops.
  The gateway only makes OUTBOUND connections; MediaMTX listens on 127.0.0.1 only.

.EXAMPLE
  .\install-gateway.ps1 -CloudUrl https://homepc.tail1234.ts.net -EnrollmentCode <code> -AllowedSubnets 192.168.0.0/24
#>
param(
    [Parameter(Mandatory)] [string]$CloudUrl,
    [Parameter(Mandatory)] [string]$EnrollmentCode,
    [Parameter(Mandatory)] [string]$AllowedSubnets,
    [string]$AllowedPorts = '',
    [string]$InstallDir = "$env:ProgramData\CameraScopeGateway"
)
$ErrorActionPreference = 'Stop'
$MtxVersion = 'v1.9.3'
$Src = Split-Path $PSScriptRoot -Parent   # ...\gateway

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated PowerShell (Run as Administrator).'
}
if (-not $CloudUrl.StartsWith('https://')) { throw 'CloudUrl must start with https://' }
foreach ($n in $AllowedSubnets.Split(',')) {
    if ($n.Trim() -notmatch '^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$') { throw "AllowedSubnets entry '$n' is not an IPv4 CIDR like 192.168.1.0/24" }
}

Step 'Checking Python'
$python = $null
foreach ($cand in @('py -3', 'python')) {
    try {
        $v = & cmd /c "$cand -c ""import sys;print(sys.version_info>=(3,10))"" 2>nul"
        if ($v -eq 'True') { $python = $cand; break }
    } catch { }
}
if (-not $python) { throw 'Python 3.10+ not found. Install it with: winget install -e --id Python.Python.3.12  (then open a NEW PowerShell window)' }

Step "Preparing $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir, "$InstallDir\state", "$InstallDir\agent", "$InstallDir\mediamtx" | Out-Null
# Only SYSTEM and Administrators may read the gateway secret/state.
& icacls $InstallDir /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
Copy-Item "$Src\agent\camerascope_gateway.py", "$Src\agent\tunnel_protocol.py", "$Src\agent\requirements.txt" "$InstallDir\agent" -Force

Step "Downloading MediaMTX $MtxVersion (official GitHub release) and verifying checksum"
$zipName = "mediamtx_${MtxVersion}_windows_amd64.zip"
$base = "https://github.com/bluenviron/mediamtx/releases/download/$MtxVersion"
$zip = Join-Path $env:TEMP $zipName
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest "$base/$zipName" -OutFile $zip -UseBasicParsing
$sums = (Invoke-WebRequest "$base/checksums.sha256" -UseBasicParsing).Content
$expected = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($zipName) } | Select-Object -First 1).Split(' ')[0].Trim()
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if (-not $expected -or $expected.ToLower() -ne $actual) { throw "MediaMTX checksum mismatch (expected $expected, got $actual) - aborting." }
Expand-Archive $zip -DestinationPath "$InstallDir\mediamtx" -Force
Remove-Item $zip

Step 'Writing MediaMTX configuration (127.0.0.1 only, reads authorised by the agent)'
$conf = Get-Content "$Src\mediamtx.yml" -Raw
$conf = $conf -replace 'authHTTPAddress: http://agent:9123/auth', 'authHTTPAddress: http://127.0.0.1:9123/auth'
$conf = $conf -replace '(?m)^apiAddress: :9997', 'apiAddress: 127.0.0.1:9997'
$conf = $conf -replace '(?m)^rtspAddress: :8554', 'rtspAddress: 127.0.0.1:8554'
[IO.File]::WriteAllText("$InstallDir\mediamtx\mediamtx.yml", $conf, (New-Object Text.UTF8Encoding $false))

Step 'Creating Python environment for the agent'
& cmd /c "$python -m venv ""$InstallDir\venv"""
if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
& "$InstallDir\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "$InstallDir\agent\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Step 'Writing gateway configuration'
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
$relay = [Convert]::ToBase64String($b).Replace('+', '').Replace('/', '').TrimEnd('=')
$envLines = @(
    "CLOUD_URL=$CloudUrl",
    "ENROLLMENT_CODE=$EnrollmentCode",
    "ALLOWED_SUBNETS=$AllowedSubnets",
    "STATE_DIR=$InstallDir\state",
    'MEDIAMTX_API=http://127.0.0.1:9997',
    'MEDIAMTX_RTSP=127.0.0.1:8554',
    'AUTH_LISTEN=127.0.0.1:9123',
    'RELAY_USER=relay',
    "RELAY_PASS=$relay"
)
if ($AllowedPorts) { $envLines += "ALLOWED_PORTS=$AllowedPorts" }
[IO.File]::WriteAllLines("$InstallDir\gateway.env", [string[]]$envLines, (New-Object Text.UTF8Encoding $false))
Copy-Item "$PSScriptRoot\run-gateway.ps1" "$InstallDir\run-gateway.ps1" -Force

Step 'Registering Scheduled Task "CameraScope Gateway" (starts at boot, restarts on failure)'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\run-gateway.ps1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'CameraScope Gateway' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName 'CameraScope Gateway'

Step 'Waiting for the gateway to enroll and connect'
$log = "$InstallDir\gateway.log"
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep 2
    if ((Test-Path $log) -and (Select-String -Path $log -Pattern 'connected to' -Quiet)) { break }
}
if ((Test-Path $log) -and (Select-String -Path $log -Pattern 'connected to' -Quiet)) {
    Write-Host 'Gateway connected. It now shows as Online in CameraScope > Manage > Gateways.' -ForegroundColor Green
} else {
    Write-Host "Gateway not connected yet - check $log" -ForegroundColor Yellow
}
Write-Host 'Keep this PC awake:  powercfg /change standby-timeout-ac 0'
