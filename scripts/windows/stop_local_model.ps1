$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
$stateFile = Join-Path $runtimeConfig.state_dir 'local_model\server_state.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'No owned local model process is recorded.'; exit 0 }
$saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
if (-not $process) { Write-Output 'Local model is already stopped.'; exit 0 }
if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $saved.start_ticks) { throw 'Process ID has been reused; nothing was stopped.' }
if ($process.ProcessName -ne 'llama-server') { throw 'Unexpected process; nothing was stopped.' }
Stop-Process -Id $process.Id
Write-Output 'Owned local model server stopped.'

