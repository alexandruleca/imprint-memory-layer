# Called after the uninstall is confirmed and before Inno removes any files.
# `imprint uninstall` disconnects clients, stops every Imprint process and
# removes the runtime and, unless -KeepData, the data (ADR 0028). A failed
# cleanup leaves a repairable install.
param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [switch]$KeepData
)
$ErrorActionPreference = 'Stop'
$log = Join-Path $InstallDir 'uninstall-cleanup.log'
try {
    $binary = Join-Path $InstallDir 'bin/imprint.exe'
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "Missing CLI: $binary. Repair Imprint, then retry uninstall." }
    $arguments = @('uninstall', '-y')
    if ($KeepData) { $arguments += '--keep-data' }
    # PowerShell 5.1 turns redirected native stderr into ErrorRecords. Ordinary
    # diagnostics are logged; the native exit code decides success.
    $ErrorActionPreference = 'Continue'
    & $binary @arguments 2>&1 | Out-File -LiteralPath $log -Encoding utf8
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw "Cleanup failed (exit $code). See $log" }
    exit 0
} catch {
    $_ | Out-File -LiteralPath $log -Encoding utf8 -Append
    exit 1
}
