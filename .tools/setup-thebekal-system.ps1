[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\user\Desktop\Slide show'
$logs = Join-Path $root '.logs'
$setupLog = Join-Path $logs 'system-setup.log'
$resultPath = Join-Path $logs 'system-setup-result.json'
$serverScript = Join-Path $root 'server.ps1'
$launcher = Join-Path $root '.tools\start-local-server.ps1'
$cloudflared = Join-Path $root '.tools\cloudflared\cloudflared.exe'
$cloudConfig = Join-Path $root '.tools\cloudflared\config.yml'
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$result = [ordered]@{ elevated=$false; localServerTaskRegistered=$false; localServerRunsAsSystem=$false; localServerHttp200=$false; localServerRecoveryTest='NOT_RUN'; cloudflaredRunsAsSystem=$false; cloudflaredRecoveryTest='NOT_RUN'; publicHttp200=$false; defenderOrAntivirusInterference=$false; firewallModified=$false; antivirusExclusionCreated=$false; frontendModified=$false; overall='FAIL' }

New-Item -ItemType Directory -Force $logs | Out-Null
function Log([string]$m) { "$(Get-Date -Format o) $m" | Tee-Object -FilePath $setupLog -Append }
function IsAdmin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
function ProcOwner([int]$id) { try { $p=Get-CimInstance Win32_Process -Filter "ProcessId=$id"; $o=Invoke-CimMethod -InputObject $p -MethodName GetOwner; "$($o.Domain)\\$($o.User)" } catch { '' } }
function WaitHttp([string]$url,[int]$seconds=90) { $end=(Get-Date).AddSeconds($seconds); do { try { if((Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 10).StatusCode -eq 200){return $true} }catch{}; Start-Sleep 3 }while((Get-Date)-lt $end); return $false }
function TaskXml([string]$name) { try { (Export-ScheduledTask -TaskName $name) } catch { "missing: $($_.Exception.Message)" } }

try {
  if(-not (IsAdmin)){ Log 'ERROR: Administrator context is required; no changes made.'; exit 2 }
  $result.elevated=$true
  Log "identity=$(whoami); ps=$psExe; version=$($PSVersionTable.PSVersion); cwd=$(Get-Location)"
  foreach($n in 'thebekal-local-server','thebekal-cloudflared'){ Log "TASK BEFORE $n`n$(TaskXml $n)"; try{Log "TASK INFO $n $(Get-ScheduledTaskInfo -TaskName $n | ConvertTo-Json -Compress)"}catch{} }
  if(!(Test-Path $serverScript) -or !(Test-Path $cloudflared) -or !(Test-Path $cloudConfig)){ throw 'Required project files are missing.' }

  @'
$root = 'C:\Users\user\Desktop\Slide show'
$log = Join-Path $root '.logs\local-server-system.log'
function WriteLaunch([string]$m){ "$(Get-Date -Format o) identity=$([Security.Principal.WindowsIdentity]::GetCurrent().Name) pid=$PID cwd=$(Get-Location) prefix=http://127.0.0.1:8787/ $m" | Add-Content -LiteralPath $log }
try { WriteLaunch 'startup-attempt'; Set-Location -LiteralPath $root; & 'C:\Users\user\Desktop\Slide show\server.ps1' -Port 8787 -Root $root; WriteLaunch 'server-exited' } catch { WriteLaunch ("fatal=" + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message); exit 1 }
'@ | Set-Content -LiteralPath $launcher -Encoding UTF8

  $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
  $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $boot=New-ScheduledTaskTrigger -AtStartup
  $serverAction=New-ScheduledTaskAction -Execute $psExe -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`"" -WorkingDirectory $root
  Register-ScheduledTask -TaskName 'thebekal-local-server' -Action $serverAction -Trigger $boot -Settings $settings -Principal $principal -Description 'Hidden SYSTEM boot task for thebekal.com loopback server; restarts after failure.' -Force | Out-Null
  $result.localServerTaskRegistered=$true

  $cloudAction=New-ScheduledTaskAction -Execute $cloudflared -Argument "--config `"$cloudConfig`" --no-autoupdate tunnel run thebekal-pc" -WorkingDirectory $root
  Register-ScheduledTask -TaskName 'thebekal-cloudflared' -Action $cloudAction -Trigger $boot -Settings $settings -Principal $principal -Description 'Hidden SYSTEM boot task for thebekal.com Cloudflare Tunnel; restarts after failure.' -Force | Out-Null
  Log "TASK AFTER server`n$(TaskXml 'thebekal-local-server')"; Log "TASK AFTER cloudflared`n$(TaskXml 'thebekal-cloudflared')"

  Stop-ScheduledTask -TaskName 'thebekal-local-server' -ErrorAction SilentlyContinue; Start-Sleep 2; Start-ScheduledTask -TaskName 'thebekal-local-server'; Start-Sleep 6
  $serverProc=Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'start-local-server\.ps1'} | Select-Object -First 1
  if($serverProc){ $result.localServerRunsAsSystem=((ProcOwner $serverProc.ProcessId) -eq 'NT AUTHORITY\\SYSTEM'); Log "server-pid=$($serverProc.ProcessId) owner=$(ProcOwner $serverProc.ProcessId)" }
  $result.localServerHttp200=WaitHttp 'http://127.0.0.1:8787/' 30
  if(!$result.localServerHttp200){ throw "SYSTEM server did not return HTTP 200. See $launcher and local-server-system.log." }

  # Stop any obsolete user-owned server host only after the SYSTEM listener works.
  Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'server\.ps1' -and $_.ProcessId -ne $serverProc.ProcessId} | ForEach-Object { if((ProcOwner $_.ProcessId) -ne 'NT AUTHORITY\\SYSTEM'){ Log "stopping obsolete user server pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force } }

  # Required server recovery test: kill only the SYSTEM launcher host and wait for Task Scheduler.
  $oldServer=$serverProc.ProcessId; Stop-Process -Id $oldServer -Force; Log "server-recovery killed=$oldServer"
  Start-Sleep 65
  $newServer=Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'start-local-server\.ps1'} | Select-Object -First 1
  if($newServer -and $newServer.ProcessId -ne $oldServer -and (ProcOwner $newServer.ProcessId) -eq 'NT AUTHORITY\\SYSTEM' -and (WaitHttp 'http://127.0.0.1:8787/' 30)){ $result.localServerRecoveryTest='PASS'; $result.localServerRunsAsSystem=$true; $result.localServerHttp200=$true } else { $result.localServerRecoveryTest='FAIL' }

  Start-ScheduledTask -TaskName 'thebekal-cloudflared' -ErrorAction SilentlyContinue; Start-Sleep 8
  $cloudProc=Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Select-Object -First 1
  if($cloudProc){ $result.cloudflaredRunsAsSystem=((ProcOwner $cloudProc.Id) -eq 'NT AUTHORITY\\SYSTEM'); Log "cloudflared-pid=$($cloudProc.Id) owner=$(ProcOwner $cloudProc.Id)" }
  if($cloudProc -and $result.cloudflaredRunsAsSystem){ $oldCloud=$cloudProc.Id; Stop-Process -Id $oldCloud -Force; Log "cloudflared-recovery killed=$oldCloud"; Start-Sleep 65; $newCloud=Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Where-Object {$_.Id -ne $oldCloud} | Select-Object -First 1; if($newCloud -and (ProcOwner $newCloud.Id) -eq 'NT AUTHORITY\\SYSTEM'){ $result.cloudflaredRecoveryTest='PASS' }else{$result.cloudflaredRecoveryTest='FAIL'} }
  $result.publicHttp200=WaitHttp 'https://thebekal.com/' 90
  try { $mp=Get-MpComputerStatus; Log "Defender mode=$($mp.AMRunningMode) antivirus=$($mp.AntivirusEnabled) realtime=$($mp.RealTimeProtectionEnabled)" }catch{}
  try { Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object {Log "antivirus=$($_.displayName)"} }catch{}
  Get-NetFirewallProfile | ForEach-Object {Log "firewall $($_.Name) enabled=$($_.Enabled)"}
  try { $hits=Get-MpThreatDetection | Where-Object {$_.Resources -match 'cloudflared|server\.ps1'}; if($hits){$result.defenderOrAntivirusInterference=$true; Log "security-hits=$($hits | ConvertTo-Json -Compress)"} }catch{}
  $result.overall=if($result.localServerRunsAsSystem -and $result.localServerHttp200 -and $result.localServerRecoveryTest -eq 'PASS' -and $result.cloudflaredRunsAsSystem -and $result.cloudflaredRecoveryTest -eq 'PASS' -and $result.publicHttp200){'PASS'}else{'FAIL'}
} catch { Log "FATAL $($_.Exception.GetType().FullName): $($_.Exception.Message)"; $result.overall='FAIL' }
finally { $result | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8; Log "RESULT $($result | ConvertTo-Json -Compress)" }
exit $(if($result.overall -eq 'PASS'){0}else{1})
