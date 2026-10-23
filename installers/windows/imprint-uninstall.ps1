# Called before Inno removes any files. A failed cleanup leaves a repairable install.
param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$log = Join-Path $InstallDir 'uninstall-cleanup.log'
try {
    $binary = Join-Path $InstallDir 'bin/imprint.exe'
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "Missing CLI: $binary. Repair Imprint, then retry uninstall." }
    # PowerShell 5.1 turns redirected native stderr into ErrorRecords. Ordinary
    # diagnostics are logged; the native exit code decides success.
    $ErrorActionPreference = 'Continue'
    & $binary disable 2>&1 | Out-File -LiteralPath $log -Encoding utf8
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw "Client cleanup failed (exit $code). See $log" }
    exit 0
} catch {
    $_ | Out-File -LiteralPath $log -Encoding utf8 -Append
    exit 1
}
