param([int]$Port = 0, [switch]$NoBrowser)
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
$previewDirectory = $runtimeConfig.state_dir
if ($Port -eq 0) { $Port = $runtimeConfig.port }
$pythonExecutable = $runtimeConfig.python
$stateFile = Join-Path $previewDirectory 'server_state.json'
$url = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Python was not found: $pythonExecutable"
}
if (Test-Path -LiteralPath $stateFile) {
    $savedState = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $runningProcess = Get-Process -Id $savedState.pid -ErrorAction SilentlyContinue
    if ($runningProcess -and $runningProcess.StartTime.ToUniversalTime().Ticks.ToString() -eq $savedState.start_ticks) {
        $url = "http://127.0.0.1:$($savedState.port)"
        try { $existingInfo = Invoke-RestMethod -Uri "$url/api/preview" -TimeoutSec 2 }
        catch { throw 'The recorded preview process is not responding. Run stop_windows.bat, then start again.' }
        if ($existingInfo.pid -ne $savedState.pid) { throw 'The preview port belongs to another service.' }
        Write-Output "Already running: $url"
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    }
}

$listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listeners) { throw "Port $Port is already in use. No existing service was stopped." }

$preflightScript = Join-Path $projectRoot 'src\task2_video_panel\preflight.py'
& $pythonExecutable -X utf8 -B $preflightScript --data-dir $runtimeConfig.data_dir
if ($LASTEXITCODE -ne 0) { throw 'Startup dependency check failed.' }
$logDirectory = Join-Path $previewDirectory 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$serverScript = Join-Path $projectRoot 'scripts\serve_windows.py'
$serverProcess = Start-Process -FilePath $pythonExecutable `
    -ArgumentList @('-X', 'utf8', '-B', ('"{0}"' -f $serverScript), '--port', "$Port") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDirectory 'server.stdout.log') `
    -RedirectStandardError (Join-Path $logDirectory 'server.stderr.log')

$serverProcess.Refresh()
$savedState = @{pid=$serverProcess.Id; start_ticks=$serverProcess.StartTime.ToUniversalTime().Ticks.ToString(); port=$Port; url=$url}
$savedState | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
$ready = $false
for ($attempt=0; $attempt -lt 60; $attempt++) {
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        Get-Content -LiteralPath (Join-Path $logDirectory 'server.stderr.log') -Tail 25
        throw "Preview exited with code $($serverProcess.ExitCode)."
    }
    try {
        $info = Invoke-RestMethod -Uri "$url/api/preview" -TimeoutSec 1
        if ($info.pid -eq $serverProcess.Id) { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw "Preview did not become ready. Inspect $logDirectory" }
Write-Output "Ready: $url"
Write-Output "PID: $($serverProcess.Id)"
if (-not $NoBrowser) { Start-Process $url }
