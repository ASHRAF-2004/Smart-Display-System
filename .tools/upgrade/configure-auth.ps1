param([Parameter(Mandatory=$true)][string]$ClientId)
$ErrorActionPreference = 'Stop'
if ($ClientId -notmatch '^\d+-[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com$') { throw 'Enter the Google Web application OAuth client ID.' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$configPath = Join-Path $projectRoot '.data\auth-config.json'
if (Test-Path -LiteralPath $configPath) {
    $backupName = Join-Path $projectRoot ('.backup\auth-config-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json')
    Copy-Item -LiteralPath $configPath -Destination $backupName
}
@{ google_client_id = $ClientId } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding ASCII
Write-Output 'Google client ID configured. Open https://thebekal.com and choose Staff Sign In with Google.'
