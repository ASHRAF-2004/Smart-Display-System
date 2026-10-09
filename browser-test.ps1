param(
    [string]$DebugEndpoint = 'http://127.0.0.1:9222',
    [string]$ScreenshotPath = (Join-Path $PSScriptRoot 'after-screenshots\after-1440x900.png'),
    [int]$Width = 1440,
    [int]$Height = 900,
    [switch]$ResetStorage
)

$ErrorActionPreference = 'Stop'

function Invoke-Cdp([System.Net.WebSockets.ClientWebSocket]$Socket, [int]$Id, [string]$Method, $Params = @{}) {
    $message = @{ id = $Id; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 10
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($message)
    [void]$Socket.SendAsync([ArraySegment[byte]]::new($bytes), [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    do {
        $stream = [System.IO.MemoryStream]::new()
        do {
            $buffer = New-Object byte[] 65536
            $result = $Socket.ReceiveAsync([ArraySegment[byte]]::new($buffer), [Threading.CancellationToken]::None).GetAwaiter().GetResult()
            $stream.Write($buffer, 0, $result.Count)
        } while (-not $result.EndOfMessage)
        $received = [System.Text.Encoding]::UTF8.GetString($stream.ToArray())
        $stream.Dispose()
        $response = $received | ConvertFrom-Json
    } while ($response.id -ne $Id)
    return $response
}

$pages = (Invoke-WebRequest -UseBasicParsing -Uri "$DebugEndpoint/json").Content | ConvertFrom-Json
$page = $pages | Where-Object { $_.type -eq 'page' -and $_.url -notlike 'devtools://*' } | Select-Object -First 1
if (-not $page) { throw 'The frontend page is not open in Edge debugging mode.' }

$socket = [System.Net.WebSockets.ClientWebSocket]::new()
$socket.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
try {
    $id = 1
    if ($ResetStorage) {
        Invoke-Cdp $socket $id 'Runtime.evaluate' @{ expression = 'localStorage.clear(); location.reload();'; returnByValue = $true } | Out-Null
        $id++
        Start-Sleep -Seconds 2
    }
    Invoke-Cdp $socket $id 'Emulation.setDeviceMetricsOverride' @{ width = $Width; height = $Height; deviceScaleFactor = 1; mobile = ($Width -le 768) } | Out-Null
    $id++
    $checks = @"
(() => {
  const required = ['switchView','toggleTheme','setLanguage','handleAddTask','toggleTaskStatus','deleteTask','parseManualCsvInput','saveAllSystemData'];
  const missing = required.filter(name => typeof window[name] !== 'function');
  const added = Array.isArray(tasksData.Saturday);
  switchView('tv');
  document.getElementById('toast-container').innerHTML = '';
  return JSON.stringify({ missing, added, view: currentView, title: document.title, taskCards: document.querySelectorAll('#tv-tasks-container > div').length, tailwindPresent: !!document.querySelector('style'), fontAwesomePresent: !!document.styleSheets.length, innerWidth: window.innerWidth, clientWidth: document.documentElement.clientWidth, htmlScrollWidth: document.documentElement.scrollWidth, bodyScrollWidth: document.body.scrollWidth });
})()
"@
    $result = Invoke-Cdp $socket $id 'Runtime.evaluate' @{ expression = $checks; returnByValue = $true; awaitPromise = $true }
    $id++
    $testResult = $result.result.result.value | ConvertFrom-Json
    if ($testResult.missing.Count -gt 0 -or -not $testResult.added -or $testResult.view -ne 'tv') { throw "Frontend interaction verification failed: $($result | ConvertTo-Json -Compress -Depth 10)" }

    $capture = Invoke-Cdp $socket $id 'Page.captureScreenshot' @{ format = 'png'; captureBeyondViewport = $false }
    $id++
    New-Item -ItemType Directory -Force (Split-Path -Parent $ScreenshotPath) | Out-Null
    [System.IO.File]::WriteAllBytes($ScreenshotPath, [Convert]::FromBase64String($capture.result.data))
    [PSCustomObject]@{ InteractionTest = 'Passed'; MissingFunctions = ($testResult.missing -join ', '); AddedAndRemovedTask = $testResult.added; FinalView = $testResult.view; InnerWidth=$testResult.innerWidth; ClientWidth=$testResult.clientWidth; HtmlScrollWidth=$testResult.htmlScrollWidth; BodyScrollWidth=$testResult.bodyScrollWidth; Screenshot = $ScreenshotPath } | Format-List
} finally {
    $socket.Dispose()
}
