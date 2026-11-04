[CmdletBinding()]
param([switch]$Force)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $PSScriptRoot 'state'
$status = $null
try { $status = Invoke-RestMethod 'http://127.0.0.1:18766/health' -TimeoutSec 2 } catch {}
if ($status -and $status.service -ne 'beeftv-codex') { throw 'Unexpected service at bridge port.' }
if ($status -and $status.activeSessions -gt 0 -and -not $Force) {
    throw 'The bridge has active sessions. Wait for completion, or explicitly use -Force.'
}
# Persist the stop request before shutdown so login/recovery triggers cannot revive it.
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
[IO.File]::WriteAllText((Join-Path $stateDir 'supervisor.disabled'), [DateTime]::UtcNow.ToString('o'))
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    $supervisorAlive = $false
    try {
        $supervisorState = Get-Content -LiteralPath (Join-Path $stateDir 'supervisor.json') -Raw | ConvertFrom-Json
        $process = Get-Process -Id $supervisorState.pid -ErrorAction Stop
        $supervisorAlive = $process.StartTime.ToFileTimeUtc().ToString() -eq [string]$supervisorState.processStartFileTime
    } catch {}
    if ($supervisorAlive) { Start-Sleep -Milliseconds 300 }
} while ($supervisorAlive -and [DateTime]::UtcNow -lt $deadline)
if ($supervisorAlive) { throw 'Supervisor has not stopped; automatic recovery is disabled. Check supervisor.log.' }

$status = $null
try { $status = Invoke-RestMethod 'http://127.0.0.1:18766/health' -TimeoutSec 2 } catch {}
if (-not $status) {
    Write-Output 'Automatic recovery is disabled; the bridge is not responding. No process was force-terminated.'
    return
}
if ($status.service -ne 'beeftv-codex') { throw 'Unexpected service at bridge port.' }
if ($status.activeSessions -gt 0 -and -not $Force) {
    throw 'A session started while stopping. The bridge was left running; run start.ps1 to re-enable recovery, or retry after the session completes.'
}
$bridgeToken = (Get-Content -LiteralPath (Join-Path $stateDir 'local-api-key') -Raw).Trim()
Invoke-RestMethod -Uri 'http://127.0.0.1:18766/shutdown' -Method Post -Headers @{Authorization = 'Bearer ' + $bridgeToken} -TimeoutSec 10 | Out-Null
$bridgeToken = $null
Write-Output 'Local Codex bridge is shutting down; automatic recovery is disabled until start.ps1 runs.'
