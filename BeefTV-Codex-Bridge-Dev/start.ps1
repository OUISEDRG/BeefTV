[CmdletBinding()]
param([int]$TimeoutSeconds = 45)
$ErrorActionPreference = 'Stop'
$codexBridgeRoot = $PSScriptRoot
$stateDir = Join-Path $codexBridgeRoot 'state'
$supervisorScript = Join-Path $codexBridgeRoot 'supervisor.py'
if (-not (Test-Path -LiteralPath $supervisorScript -PathType Leaf)) { throw 'Missing supervisor.py.' }
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$disabledPath = Join-Path $stateDir 'supervisor.disabled'
if (Test-Path -LiteralPath $disabledPath) { Remove-Item -LiteralPath $disabledPath -Force }

# Starting a healthy bridge must also restore its supervisor.
$task = Get-ScheduledTask -TaskName 'BeefTV-Codex-Bridge' -ErrorAction SilentlyContinue
if ($task) {
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Arguments -notlike ('*"' + $supervisorScript + '"*')) {
        throw 'Scheduled task belongs to another bridge installation.'
    }
    Enable-ScheduledTask -InputObject $task | Out-Null
    Start-ScheduledTask -InputObject $task
} else {
    Start-Process -FilePath 'D:\Python\pythonw.exe' -ArgumentList @('-B', '-X', 'utf8', ('"' + $supervisorScript + '"')) -WorkingDirectory $codexBridgeRoot -WindowStyle Hidden | Out-Null
}
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
while ([DateTime]::UtcNow -lt $deadline) {
    $supervisorAlive = $false
    try {
        $supervisorState = Get-Content -LiteralPath (Join-Path $stateDir 'supervisor.json') -Raw | ConvertFrom-Json
        $process = Get-Process -Id $supervisorState.pid -ErrorAction Stop
        $supervisorAlive = $process.StartTime.ToFileTimeUtc().ToString() -eq [string]$supervisorState.processStartFileTime
    } catch {}
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:18766/health' -TimeoutSec 2
        if ($status.service -eq 'beeftv-codex' -and $status.ready -and $supervisorAlive) {
            Write-Output 'Local Codex bridge and supervisor are ready (no inference test).'
            return
        }
        if ($status.service -ne 'beeftv-codex') { throw 'Unexpected service at bridge port.' }
    } catch {}
    Start-Sleep -Milliseconds 500
}
throw 'Bridge is not ready yet; the supervisor will keep retrying. Check state\supervisor.log and state\server-error.log.'
