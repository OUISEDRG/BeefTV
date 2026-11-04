$ErrorActionPreference = 'Stop'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Dreama bridge startup failed.' }
$localKey = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'state\local-api-key') -Raw).Trim()
Start-Process ('http://127.0.0.1:18765/console#' + $localKey)
