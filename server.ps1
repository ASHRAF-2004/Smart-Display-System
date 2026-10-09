param(
    [int]$Port = 8787,
    [string]$Root = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$logDirectory = Join-Path $Root '.logs'
$logFile = Join-Path $logDirectory 'local-server.log'
$python = Join-Path $Root '.tools\python\python.exe'
$application = Join-Path $Root 'app.py'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Write-ServerLog([string]$Message) {
    $line = "$(Get-Date -Format o) $Message`r`n"
    [System.IO.File]::AppendAllText($logFile, $line)
}

try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Portable Python runtime not found: $python" }
    if (-not (Test-Path -LiteralPath $application -PathType Leaf)) { throw "Application server not found: $application" }
    Write-ServerLog "Starting SQLite/SSE application on http://127.0.0.1:$Port/ identity=$([Security.Principal.WindowsIdentity]::GetCurrent().Name) pid=$PID cwd=$(Get-Location)"
    & $python $application --port $Port
    $exitCode = $LASTEXITCODE
    Write-ServerLog "Application process exited code=$exitCode"
    exit $exitCode
} catch {
    Write-ServerLog "FATAL application startup: $($_.Exception.Message)"
    throw
}
