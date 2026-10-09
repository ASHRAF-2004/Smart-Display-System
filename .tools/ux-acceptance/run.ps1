$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $projectRoot '.tools\python\python.exe'
$harness = Join-Path $PSScriptRoot 'ux_acceptance.py'
Set-Location -LiteralPath $projectRoot
& $python $harness
exit $LASTEXITCODE
