$ErrorActionPreference = 'Stop'
$root = 'C:\Users\user\Desktop\Slide show'
$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function Register-ThebekalSystemTask([string]$Name, [string]$Executable, [string]$Arguments, [string]$Description) {
    $action = New-ScheduledTaskAction -Execute $Executable -Argument $Arguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $Description -Force | Out-Null
}

Register-ThebekalSystemTask -Name 'thebekal-local-server' -Executable $powerShell -Arguments '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\user\Desktop\Slide show\server.ps1" -Port 8787' -Description 'Starts thebekal.com local loopback server at Windows boot and restarts it after failure.'
Register-ThebekalSystemTask -Name 'thebekal-cloudflared' -Executable "$root\.tools\cloudflared\cloudflared.exe" -Arguments '--config "C:\Users\user\Desktop\Slide show\.tools\cloudflared\config.yml" --no-autoupdate tunnel run thebekal-pc' -Description 'Starts thebekal.com Cloudflare Tunnel at Windows boot and restarts it after failure.'

Get-ScheduledTask -TaskName 'thebekal-local-server','thebekal-cloudflared' | Select-Object TaskName,State,Principal,Triggers,Actions,Settings | Format-List
