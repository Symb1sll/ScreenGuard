$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
& $runtimeConfig.python -X utf8 -B (Join-Path $projectRoot 'src\task2_video_panel\manage.py') --data-dir $runtimeConfig.data_dir create-admin
exit $LASTEXITCODE
