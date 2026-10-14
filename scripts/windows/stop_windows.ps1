$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
$stateFile = Join-Path $runtimeConfig.state_dir 'server_state.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'No preview process is recorded.'; exit 0 }
$savedState = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$serverProcess = Get-Process -Id $savedState.pid -ErrorAction SilentlyContinue
if (-not $serverProcess) { Write-Output 'The preview is already stopped.'; exit 0 }
if ($serverProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne $savedState.start_ticks) {
    throw 'The recorded process ID has been reused. No process was stopped.'
}
$url = "http://127.0.0.1:$($savedState.port)"
try {
    $info = Invoke-RestMethod -Uri "$url/api/preview" -TimeoutSec 2
    if ($info.pid -ne $savedState.pid) { throw 'Unexpected service on the recorded port.' }
    $sessionInfo = Invoke-RestMethod -Uri "$url/api/bootstrap" -SessionVariable previewSession -TimeoutSec 2
    Invoke-RestMethod -Method Post -Uri "$url/api/shutdown" -WebSession $previewSession -Headers @{'X-SG-CSRF'=$sessionInfo.csrf} -TimeoutSec 2 | Out-Null
    $serverProcess.WaitForExit(15000) | Out-Null
} catch { Write-Output 'Graceful shutdown unavailable; checking the owned process tree.' }
$serverProcess.Refresh()
if (-not $serverProcess.HasExited) {
    if ($serverProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne $savedState.start_ticks) { throw 'Process ownership changed.' }
    & taskkill.exe /PID $serverProcess.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the recorded preview process tree.' }
}
Write-Output 'Windows preview stopped.'
