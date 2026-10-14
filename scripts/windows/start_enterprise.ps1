param([int]$Port = 0)
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
if ($Port -eq 0) { $Port = $runtimeConfig.port }
$projectDirectory = Join-Path $projectRoot 'src\task2_video_panel'
& $runtimeConfig.python -X utf8 -B (Join-Path $projectDirectory 'preflight.py') --data-dir $runtimeConfig.data_dir
if ($LASTEXITCODE -ne 0) { throw 'Startup dependency check failed.' }
& $runtimeConfig.python -X utf8 -B (Join-Path $projectRoot 'deploy\supervise.py') --accounts --port $Port --data-dir $runtimeConfig.data_dir
exit $LASTEXITCODE
