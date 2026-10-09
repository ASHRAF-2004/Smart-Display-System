$ErrorActionPreference='Stop'
$root='C:\Users\user\Desktop\Slide show'
$ps="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$launcher="$root\server-system-launcher.ps1"
@'
$root = 'C:\Users\user\Desktop\Slide show'
$log = Join-Path $root '.logs\server-system-launcher.log'
try {
  "$(Get-Date -Format o) launcher started as $([Security.Principal.WindowsIdentity]::GetCurrent().Name)" | Add-Content -LiteralPath $log
  Set-Location -LiteralPath $root
  & "$root\server.ps1" -Port 8787 -Root $root
} catch {
  "$(Get-Date -Format o) launcher fatal: $($_ | Out-String)" | Add-Content -LiteralPath $log
  exit 1
}
'@ | Set-Content -LiteralPath $launcher -Encoding UTF8
$action=New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcher`"" -WorkingDirectory $root
$trigger=New-ScheduledTaskTrigger -AtStartup
$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'thebekal-local-server' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Starts thebekal.com local loopback server at Windows boot and restarts it after failure.' -Force | Out-Null
Stop-ScheduledTask -TaskName 'thebekal-local-server' -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName 'thebekal-local-server'
Start-Sleep -Seconds 5
Get-Content "$root\.logs\server-system-launcher.log" -Tail 30 -ErrorAction SilentlyContinue
