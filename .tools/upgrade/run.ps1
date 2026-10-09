param([switch]$SkipRecovery)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
& '.\.tools\python\python.exe' '.\.tools\upgrade\acceptance.py'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $SkipRecovery) {
    & '.\.tools\python\python.exe' '.\.tools\upgrade\production_acceptance.py'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Output 'Automated checks passed. Real public Google sign-in is a separate required acceptance gate.'
