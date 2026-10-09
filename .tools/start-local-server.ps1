$root = 'C:\Users\user\Desktop\Slide show'
$log = Join-Path $root '.logs\local-server-system.log'
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
function WriteLaunch([string]$m) { "$(Get-Date -Format o) identity=$([Security.Principal.WindowsIdentity]::GetCurrent().Name) launcherPid=$PID cwd=$(Get-Location) prefix=http://127.0.0.1:8787/ $m" | Add-Content -LiteralPath $log }
Set-Location -LiteralPath $root
while ($true) {
    try {
        WriteLaunch 'starting-server-child'
        $child = Start-Process -FilePath $ps -ArgumentList '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\user\Desktop\Slide show\server.ps1" -Port 8787' -WorkingDirectory $root -WindowStyle Hidden -PassThru
        WriteLaunch "server-child-pid=$($child.Id)"
        Wait-Process -Id $child.Id
        WriteLaunch "server-child-exited pid=$($child.Id); retrying in 5 seconds"
    } catch { WriteLaunch ("watchdog-error=" + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }
    Start-Sleep -Seconds 5
}
