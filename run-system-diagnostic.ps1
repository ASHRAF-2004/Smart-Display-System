$ErrorActionPreference='Stop'
$root='C:\Users\user\Desktop\Slide show'
$ps="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$action=New-ScheduledTaskAction -Execute $ps -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\user\Desktop\Slide show\.tools\diagnostics\system-task-diagnostic.ps1"' -WorkingDirectory $root
$principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'thebekal-system-diagnostic' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'thebekal-system-diagnostic'
Start-Sleep -Seconds 5
Get-ScheduledTaskInfo -TaskName 'thebekal-system-diagnostic' | Format-List
Get-Content "$root\.logs\system-task-diagnostic.log" -Tail 20 -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'thebekal-system-diagnostic' -Confirm:$false
