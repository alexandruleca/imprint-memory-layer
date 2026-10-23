# Native regression: Windows PowerShell 5.1 must log ordinary native stderr
# without treating it as failure, and must still propagate a nonzero exit.
param([string]$FixtureBinary)

$ErrorActionPreference = 'Stop'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('imprint-setup-test-' + [guid]::NewGuid())
$helper = (Resolve-Path "$PSScriptRoot/../installers/windows/imprint-setup.ps1").Path
New-Item -ItemType Directory -Path $root -Force | Out-Null
try {
    $fixture = Join-Path $root 'fixture.go'
    @'
package main
import ("fmt"; "os"; "encoding/json")
func main() {
    if len(os.Args)>1 && os.Args[1]=="--version" { fmt.Println("uv fixture"); return }
    if path := os.Getenv("IMPRINT_SETUP_TEST_ARGS"); path != "" {
        f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
        if err != nil { os.Exit(1) }; json.NewEncoder(f).Encode(os.Args[1:]); f.Close()
    }
    fmt.Fprintln(os.Stderr, "ordinary progress on stderr")
    if os.Getenv("IMPRINT_SETUP_TEST_FAIL")=="1" { os.Exit(23) }
}
'@ | Set-Content $fixture -Encoding ascii
    $binary = Join-Path $root 'fixture.exe'
    if ($FixtureBinary) {
        Copy-Item $FixtureBinary $binary
    } else {
        & go build -o $binary $fixture
        if ($LASTEXITCODE -ne 0) { throw 'Could not compile setup fixture' }
    }
    foreach ($fail in @(0, 1)) {
        $dest = Join-Path $root "install with spaces $fail"
        New-Item -ItemType Directory -Path "$dest/bin" -Force | Out-Null
        Copy-Item $binary "$dest/bin/imprint.exe"
        Copy-Item $binary "$dest/bin/uv.exe"
        Copy-Item $helper "$dest/imprint-setup.ps1"
        $env:IMPRINT_SETUP_TEST_FAIL = "$fail"
        $env:IMPRINT_SETUP_TEST_ARGS = Join-Path $dest 'calls.jsonl'
        "Ubuntu Test|claude-code`nUbuntu Test|codex" | Set-Content "$dest/.wsl-integrations.txt" -Encoding utf8
        $child = Start-Process powershell.exe -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$dest/imprint-setup.ps1`"", '-InstallDir', "`"$dest`"", '-Profile', 'cpu', '-NoLlm'
        ) -WorkingDirectory $dest -RedirectStandardOutput "$dest/stdout.log" -RedirectStandardError "$dest/stderr.log" -PassThru
        # Windows PowerShell 5.1 needs the handle opened before the process
        # exits to retain ExitCode reliably after WaitForExit/Refresh.
        $null = $child.Handle
        if (-not $child.WaitForExit(30000)) {
            taskkill /PID $child.Id /T /F
            throw 'Setup regression fixture timed out'
        }
        $child.Refresh()
        if ($child.ExitCode -ne $fail) {
            Get-Content "$dest/stdout.log", "$dest/stderr.log"
            throw "Expected exit $fail, got $($child.ExitCode)"
        }
        if ((Get-Content "$dest/.setup-result").Trim() -ne "$fail") { throw 'Wrong setup result' }
        if ((Test-Path "$dest/.first-run.done") -ne ($fail -eq 0)) { throw 'Wrong completion sentinel' }
        if ((Get-Content "$dest/first-run.log" -Raw) -notmatch 'ordinary progress on stderr') {
            throw 'Native stderr was not retained'
        }
        if ($fail -eq 0) {
            $calls = @(Get-Content $env:IMPRINT_SETUP_TEST_ARGS | ForEach-Object { ,(ConvertFrom-Json $_) })
            $wslCalls = @($calls | Where-Object { $_[0] -eq 'wsl' })
            if ($wslCalls.Count -ne 2) { throw 'Saved WSL selections were not replayed' }
            if ($wslCalls[0][3] -cne 'Ubuntu Test' -or $wslCalls[1][5] -cne 'codex') {
                throw 'Saved WSL distribution/target arguments were not preserved'
            }
        }
    }
    Write-Host 'Windows PowerShell setup stderr and nonzero-exit regressions passed'
} finally {
    Remove-Item Env:IMPRINT_SETUP_TEST_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:IMPRINT_SETUP_TEST_ARGS -ErrorAction SilentlyContinue
    Remove-Item $root -Recurse -Force
}
