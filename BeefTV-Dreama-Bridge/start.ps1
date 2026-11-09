$ErrorActionPreference = 'Stop'
$bridgeRoot = $PSScriptRoot
try {
    $status = Invoke-RestMethod 'http://127.0.0.1:18765/health' -TimeoutSec 2
    if ($status.service -eq 'dreama-preflight') { Write-Output ('Dreama video bridge is running. Execution mode: ' + $status.executionMode); exit 0 }
    throw 'Port 18765 is used by another service.'
} catch {
    if (Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 18765 is occupied.' }
}
$pythonExe = (Get-Command python.exe).Source
Start-Process -FilePath $pythonExe -ArgumentList @(('"' + (Join-Path $bridgeRoot 'server.py') + '"')) -WorkingDirectory $bridgeRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $bridgeRoot 'state\server.log') -RedirectStandardError (Join-Path $bridgeRoot 'state\server-error.log') | Out-Null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:18765/health' -TimeoutSec 1
        if ($status.service -eq 'dreama-preflight') { Write-Output ('Dreama video bridge started. Execution mode: ' + $status.executionMode); exit 0 }
    } catch {}
}
throw 'Bridge startup failed. Check state\server-error.log.'
