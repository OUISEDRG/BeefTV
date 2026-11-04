[CmdletBinding()]
param([string]$PythonExecutable = 'D:\Python\pythonw.exe')
$ErrorActionPreference = 'Stop'
$codexBridgeRoot = $PSScriptRoot
$supervisorScript = Join-Path $codexBridgeRoot 'supervisor.py'
foreach ($required in @($PythonExecutable, $supervisorScript, (Join-Path $codexBridgeRoot 'server.py'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw ('Missing file: ' + $required) }
}
$taskName = 'BeefTV-Codex-Bridge'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Count -ne 1 -or $existing.Actions[0].Arguments -notlike ('*"' + $supervisorScript + '"*'))) {
    throw 'A scheduled task with this name belongs to another installation.'
}
$backupDir = Join-Path $codexBridgeRoot ('backups\service-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
if ($existing) { Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $backupDir 'previous-task.xml') -Encoding Unicode }
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $PythonExecutable -Argument ('-B -X utf8 "' + $supervisorScript + '"') -WorkingDirectory $codexBridgeRoot
$login = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
# A repeating trigger also recovers the supervisor itself after an external termination.
$recovery = New-ScheduledTaskTrigger -Once -At ([DateTime]::Now.AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger @($login, $recovery) -Settings $settings -Principal $principal -Description 'Keeps the local BeefTV Codex bridge available. Health checks and restart only; never replays model requests.'
Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
& (Join-Path $codexBridgeRoot 'start.ps1')

# Retire the old once-at-login launcher only after the supervised service is ready.
$legacy = Join-Path ([Environment]::GetFolderPath('Startup')) 'BeefTV-Codex-Bridge.vbs'
if (Test-Path -LiteralPath $legacy -PathType Leaf) {
    $legacyText = Get-Content -LiteralPath $legacy -Raw
    if ($legacyText.IndexOf($codexBridgeRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Copy-Item -LiteralPath $legacy -Destination (Join-Path $backupDir 'BeefTV-Codex-Bridge.vbs')
        Remove-Item -LiteralPath $legacy
    } else { Write-Warning 'An unrelated startup launcher was left unchanged.' }
}
Write-Output ('Installed per-user bridge recovery task. Backup: ' + $backupDir)
