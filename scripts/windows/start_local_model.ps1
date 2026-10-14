param([string]$ModelPath = '', [string]$ServerPath = '', [int]$Port = 0, [string]$Mmproj = '', [switch]$Vision)
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $projectRoot 'scripts\windows\runtime_config.ps1')
$runtimeConfig = Get-ScreenGuardRuntime
if (-not $ModelPath) { $ModelPath = $runtimeConfig.model_path }
if (-not $ServerPath) { $ServerPath = $runtimeConfig.server_path }
if ($Port -eq 0) { $Port = $runtimeConfig.model_port }
if ($Vision -and -not $Mmproj) { $Mmproj = $runtimeConfig.mmproj }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
foreach ($inputPath in @($ModelPath, $ServerPath)) {
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw "File not found: $inputPath" }
    if ($inputPath.Contains('"')) { throw 'Paths cannot contain double quotes.' }
}
if ($Mmproj -and ((-not (Test-Path -LiteralPath $Mmproj -PathType Leaf)) -or $Mmproj.Contains('"'))) { throw 'Invalid mmproj path.' }
$runtimeDirectory = Join-Path $runtimeConfig.state_dir 'local_model'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$stateFile = Join-Path $runtimeDirectory 'server_state.json'
if (Test-Path -LiteralPath $stateFile) {
    $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $saved.start_ticks) {
        if ([bool]$saved.vision -ne [bool]$Mmproj) { throw 'The owned server uses a different text/vision mode. Run stop_local_model.bat, then start the intended mode.' }
        Write-Output "Owned model process already running (PID $($saved.pid)); loading can take several minutes."
        exit 0
    }
}
$listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listeners) { throw "Port $Port is in use. No existing process was changed." }
$arguments = @('-m', ('"{0}"' -f $ModelPath), '--host', '127.0.0.1', '--port', "$Port",
    '--alias', 'qwen-local', '--threads', [string]$runtimeConfig.model_threads, '--threads-batch', [string]$runtimeConfig.model_threads, '--ctx-size', '4096', '--parallel', '1', '--cache-ram', '0',
    '--offline', '--fit', 'on', '--fit-target', '1024', '--reasoning', 'off', '--reasoning-budget', '0',
    '--cors-origins', 'http://127.0.0.1:8765,http://localhost:8765', '--no-webui', '--no-slots')
if ($Mmproj) { $arguments += @('--mmproj', ('"{0}"' -f $Mmproj), '--image-min-tokens', '1024', '--image-max-tokens', '1536') }
else { $arguments += '--no-mmproj' }
# Runtime/cache paths belong to this project; model assets are read-only inputs.
$previousEnvironment = @{}
$cacheNames = @('TEMP', 'TMP', 'TMPDIR', 'HF_HOME', 'LLAMA_CACHE', 'XDG_CACHE_HOME', 'CUDA_CACHE_PATH', 'LLAMA_API_KEY')
try {
    foreach ($name in $cacheNames) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($name -ne 'LLAMA_API_KEY') { [Environment]::SetEnvironmentVariable($name, $runtimeDirectory, 'Process') }
    }
    $modelKey = (& $runtimeConfig.python -X utf8 -B (Join-Path $projectRoot 'scripts\local_model_credentials.py') $Port)
    if ($LASTEXITCODE -ne 0 -or -not $modelKey) { throw 'Unable to prepare the encrypted local API credential.' }
    [Environment]::SetEnvironmentVariable('LLAMA_API_KEY', $modelKey.Trim(), 'Process')
    $modelKey = $null
    $process = Start-Process -FilePath $ServerPath -ArgumentList $arguments -WorkingDirectory $runtimeDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'stderr.log')
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) { throw "Model server exited. Check $runtimeDirectory\stderr.log" }
    @{pid=$process.Id;start_ticks=$process.StartTime.ToUniversalTime().Ticks.ToString();port=$Port;vision=[bool]$Mmproj} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
    Write-Output "Model process started (PID $($process.Id)). Wait for loading, then use the workbench API test."
    Write-Output "Base URL: http://127.0.0.1:$Port/v1 ; model: qwen-local"
    Write-Output 'The local API is authenticated. Reload the workbench page before testing.'
    Write-Output "Logs: $runtimeDirectory"
} finally {
    foreach ($name in $cacheNames) { [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process') }
}

