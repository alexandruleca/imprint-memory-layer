# Native regression: Windows PowerShell 5.1 must log ordinary native stderr
# without treating it as failure, and must still propagate a nonzero exit.
$ErrorActionPreference = 'Stop'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('imprint-setup-test-' + [guid]::NewGuid())
$helper = (Resolve-Path "$PSScriptRoot/../installers/windows/imprint-setup.ps1").Path
New-Item -ItemType Directory -Path $root -Force | Out-Null
try {
    $fixture = Join-Path $root 'fixture.go'
    @'
package main
import ("fmt"; "os")
func main() {
    if len(os.Args)>1 && os.Args[1]=="--version" { fmt.Println("uv fixture"); return }
    fmt.Fprintln(os.Stderr, "ordinary progress on stderr")
    if os.Getenv("IMPRINT_SETUP_TEST_FAIL")=="1" { os.Exit(23) }
}
'@ | Set-Content $fixture -Encoding ascii
    $binary = Join-Path $root 'fixture.exe'
    & go build -o $binary $fixture
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile setup fixture' }
    foreach ($fail in @(0, 1)) {
        $dest = Join-Path $root "install with spaces $fail"
        New-Item -ItemType Directory -Path "$dest/bin" -Force | Out-Null
        Copy-Item $binary "$dest/bin/imprint.exe"
        Copy-Item $binary "$dest/bin/uv.exe"
        $env:IMPRINT_SETUP_TEST_FAIL = "$fail"
        $child = Start-Process powershell.exe -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$helper`"", '-InstallDir', "`"$dest`"", '-Profile', 'cpu', '-NoLlm'
        ) -PassThru
        if (-not $child.WaitForExit(30000)) {
            taskkill /PID $child.Id /T /F
            throw 'Setup regression fixture timed out'
        }
        $child.Refresh()
        if ($child.ExitCode -ne $fail) { throw "Expected exit $fail, got $($child.ExitCode)" }
        if ((Get-Content "$dest/.setup-result").Trim() -ne "$fail") { throw 'Wrong setup result' }
        if ((Test-Path "$dest/.first-run.done") -ne ($fail -eq 0)) { throw 'Wrong completion sentinel' }
        if ((Get-Content "$dest/first-run.log" -Raw) -notmatch 'ordinary progress on stderr') {
            throw 'Native stderr was not retained'
        }
    }
    Write-Host 'Windows PowerShell setup stderr and nonzero-exit regressions passed'
} finally {
    Remove-Item Env:IMPRINT_SETUP_TEST_FAIL -ErrorAction SilentlyContinue
    Remove-Item $root -Recurse -Force
}
