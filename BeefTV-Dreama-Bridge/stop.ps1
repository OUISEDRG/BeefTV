$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'state\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No bridge PID file.'; exit 0 }
$bridgePid = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $bridgePid"
if (-not $process) { Write-Output 'Bridge is already stopped.'; exit 0 }
$expected = Join-Path $PSScriptRoot 'server.py'
if ($process.CommandLine -notlike "*$expected*" -or $process.Name -notlike 'python*.exe') { throw 'PID belongs to another process; refusing to stop it.' }
Stop-Process -Id $bridgePid
Write-Output 'Bridge stopped. Incomplete preflights will require reconciliation.'
