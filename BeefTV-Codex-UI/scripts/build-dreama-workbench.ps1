[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BridgeSource,
    [Parameter(Mandatory=$true)][string]$CodexSource,
    [Parameter(Mandatory=$true)][string]$Output,
    [string]$ToolchainScript = 'D:\BeefTV-Codex-UI-tools\env.ps1',
    [string]$UpstreamTag = 'v2.1.1',
    [string]$Customization = 'dreama-workbench.1'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$dreamaRepository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$dreamaBuild = [IO.Path]::GetFullPath((Join-Path $dreamaRepository 'backend\cmd\desktop\build\bin'))
if (-not $dreamaBuild.StartsWith($dreamaRepository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output escaped repository.' }
if (Test-Path -LiteralPath $Output) { throw 'Use a new output package directory.' }
if (& git -C $dreamaRepository status --porcelain) { throw 'Commit reviewed application changes before building.' }
if (& git -C $BridgeSource status --porcelain) { throw 'Commit reviewed bridge changes before building.' }
. $ToolchainScript
$env:BEEFTV_DREAMA_MANIFEST = Join-Path $BridgeSource 'dreama-modalities-manifest.json'
Push-Location (Join-Path $dreamaRepository 'backend')
try {
    & go test ./internal/protocol -run '^TestDreamaReleaseContract$' -count=1
    if ($LASTEXITCODE -ne 0) { throw 'Bridge plugin contract does not match the application.' }
} finally { Pop-Location }
# The dedicated distribution is updated from reviewed local packages only.
$env:BEEFTV_UPDATER_PUBLIC_KEY = ''
$env:BEEFTV_EXTRA_LDFLAGS = ''
& (Join-Path $dreamaRepository 'scripts\build-beeftv-windows-release.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Complete desktop build failed.' }
& python (Join-Path $PSScriptRoot 'dreama_release.py') package --repository $dreamaRepository --bridge $BridgeSource --codex $CodexSource --build $dreamaBuild --output $Output --tag $UpstreamTag --customization $Customization
if ($LASTEXITCODE -ne 0) { throw 'Dreama release packaging failed.' }
