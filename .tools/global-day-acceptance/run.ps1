$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root '.tools\python\python.exe'
$harness = Join-Path $PSScriptRoot 'global_day_acceptance.py'
Set-Location -LiteralPath $root
& $python $harness
exit $LASTEXITCODE
