# Native Windows regression: a ready install launches the GUI directly with
# correctly quoted source, data and writable Python paths. No real profiles.
param([string]$FixtureBinary)

$ErrorActionPreference = 'Stop'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("imprint launcher's test " + [guid]::NewGuid())
$oldCapture = $env:IMPRINT_TEST_DESKTOP_CAPTURE
$oldDataDir = $env:IMPRINT_DATA_DIR
New-Item -ItemType Directory -Path "$root/bin", "$root/.venv/Scripts" -Force | Out-Null
try {
    $fixture = Join-Path $root 'fixture.go'
    @'
package main
import ("encoding/json"; "os")
func main() {
    b, _ := json.Marshal(os.Args)
    if err := os.WriteFile(os.Getenv("IMPRINT_TEST_DESKTOP_CAPTURE"), b, 0600); err != nil { os.Exit(1) }
}
'@ | Set-Content $fixture -Encoding ascii
    if ($FixtureBinary) {
        Copy-Item $FixtureBinary "$root/bin/imprint-ui.exe"
    } else {
        & go build '-ldflags=-H=windowsgui' -o "$root/bin/imprint-ui.exe" $fixture
        if ($LASTEXITCODE -ne 0) { throw 'Could not compile desktop fixture' }
    }
    New-Item -ItemType File -Path "$root/bin/imprint.exe", "$root/.first-run.done", "$root/.venv/Scripts/python.exe" | Out-Null
    $launcher = Join-Path $root 'imprint-launcher.ps1'
    Copy-Item "$PSScriptRoot/../installers/windows/imprint-launcher.ps1" $launcher
    $env:IMPRINT_TEST_DESKTOP_CAPTURE = Join-Path $root 'captured arguments.json'
    $env:IMPRINT_DATA_DIR = Join-Path $root 'custom data'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $launcher
    if ($LASTEXITCODE -ne 0) { throw 'Desktop launcher failed' }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path $env:IMPRINT_TEST_DESKTOP_CAPTURE)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'GUI fixture was not launched' }
        Start-Sleep -Milliseconds 100
    }
    $captured = Get-Content $env:IMPRINT_TEST_DESKTOP_CAPTURE -Raw | ConvertFrom-Json
    $expected = @(
        (Join-Path $root 'bin\imprint-ui.exe'),
        "--project-dir=$root",
        "--data-dir=$env:IMPRINT_DATA_DIR",
        "--python=$(Join-Path $root '.venv\Scripts\python.exe')"
    )
    if (($captured -join "`n") -cne ($expected -join "`n")) {
        throw "Wrong desktop launch arguments: $($captured | ConvertTo-Json -Compress); expected: $($expected | ConvertTo-Json -Compress)"
    }
    Write-Host 'Windows desktop shortcut launches the GUI with preserved paths'
} finally {
    $env:IMPRINT_TEST_DESKTOP_CAPTURE = $oldCapture
    $env:IMPRINT_DATA_DIR = $oldDataDir
    Remove-Item $root -Recurse -Force
}
