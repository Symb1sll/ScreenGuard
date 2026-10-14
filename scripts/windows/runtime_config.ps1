$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Shared process-local configuration. Optional runtime_config.json overrides defaults;
# environment variables take precedence. No machine environment is modified.
function Get-ScreenGuardRuntime {
    $values = @{
        python = (Join-Path $projectRoot '.venv\Scripts\python.exe')
        data_dir = (Join-Path $projectRoot 'windows_preview\runtime\data')
        state_dir = (Join-Path $projectRoot 'windows_preview')
        port = 8765
        model_port = 8088
        model_threads = 4
        model_path = ''
        server_path = ''
        mmproj = ''
    }
    $configPath = Join-Path $projectRoot 'config\runtime_config.json'
    if (-not (Test-Path -LiteralPath $configPath)) { $configPath = Join-Path $projectRoot 'runtime_config.json' }
    if (Test-Path -LiteralPath $configPath) {
        $saved = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($item in $saved.PSObject.Properties) {
            if (-not $values.ContainsKey($item.Name)) { throw "Unknown runtime option: $($item.Name)" }
            $values[$item.Name] = $item.Value
        }
    }
    $mapping = @{python='SCREEN_GUARD_PYTHON';data_dir='SCREEN_GUARD_DATA_DIR';state_dir='SCREEN_GUARD_STATE_DIR';port='SCREEN_GUARD_PORT';model_port='SCREEN_GUARD_MODEL_PORT';model_threads='SCREEN_GUARD_MODEL_THREADS';model_path='SCREEN_GUARD_LOCAL_MODEL';server_path='SCREEN_GUARD_LLAMA_SERVER';mmproj='SCREEN_GUARD_MMPROJ'}
    foreach ($key in $mapping.Keys) {
        $value = [Environment]::GetEnvironmentVariable($mapping[$key], 'Process')
        if ($value) { $values[$key] = $value }
    }
    foreach ($key in @('port','model_port')) {
        $values[$key] = [int]$values[$key]
        if ($values[$key] -lt 1024 -or $values[$key] -gt 65535) { throw "Invalid port: $key" }
    }
    foreach ($key in @('data_dir','state_dir','model_path','server_path','mmproj','python')) {
        if ([string]$values[$key] -match '[\r\n\x00"]') { throw "Invalid path: $key" }
    }
    $values.model_threads = [int]$values.model_threads
    if ($values.model_threads -lt 1 -or $values.model_threads -gt 64) { throw 'model_threads must be between 1 and 64.' }
    $projectPrefix = [IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
    foreach ($key in @('python','model_path','server_path','mmproj')) {
        if ($values[$key] -and -not [IO.Path]::IsPathRooted($values[$key])) { $values[$key] = Join-Path $projectRoot $values[$key] }
    }
    foreach ($key in @('data_dir','state_dir')) {
        if (-not [IO.Path]::IsPathRooted($values[$key])) { $values[$key] = Join-Path $projectRoot $values[$key] }
        $values[$key] = [IO.Path]::GetFullPath($values[$key])
        if (-not $values[$key].StartsWith($projectPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw "Runtime path must remain inside this project: $key" }
    }
    foreach ($key in $mapping.Keys) { [Environment]::SetEnvironmentVariable($mapping[$key], [string]$values[$key], 'Process') }
    return [pscustomobject]$values
}
