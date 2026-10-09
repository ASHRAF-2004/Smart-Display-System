$root = 'C:\Users\user\Desktop\Slide show'
$log = Join-Path $root '.logs\system-task-diagnostic.log'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    @(
        "timestamp=$(Get-Date -Format o)",
        "whoami=$(whoami)",
        "identity=$($identity.Name)",
        "cwd=$(Get-Location)",
        "powershell=$($PSHOME)\\powershell.exe",
        "psversion=$($PSVersionTable.PSVersion)",
        "serverReadable=$(Test-Path -LiteralPath (Join-Path $root 'server.ps1') -PathType Leaf)",
        "projectReadable=$(Test-Path -LiteralPath $root -PathType Container)"
    ) | Add-Content -LiteralPath $log
} catch {
    "timestamp=$(Get-Date -Format o) diagnostic-error=$($_.Exception.GetType().FullName): $($_.Exception.Message)" | Add-Content -LiteralPath $log -ErrorAction SilentlyContinue
    exit 1
}
