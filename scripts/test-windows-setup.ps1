# Native regression: Windows PowerShell 5.1 must log ordinary native stderr
# without treating it as failure, and must still propagate a nonzero exit.
# Setup ends by restarting a running dashboard; that step cannot fail setup.
param([string]$FixtureBinary)

$ErrorActionPreference = 'Stop'
# CI's Windows VMs share a busy host: the same allowance as the other tests
# (CONTRIBUTING.md, Time limits in tests).
$slack = if ($env:IMPRINT_TEST_TIME_SCALE) { [double]$env:IMPRINT_TEST_TIME_SCALE } elseif ($env:CI) { 3 } else { 1 }
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
    if os.Getenv("NO_COLOR") == "" { fmt.Println("\033[0;36m[*]\033[0m colored progress") }
    fmt.Println("\u2192 utf-8 progress")
    for i := 0; i < 300; i++ { fmt.Println("progress line", i) }
    if os.Getenv("IMPRINT_SETUP_TEST_FAIL")=="1" { os.Exit(23) }
    if len(os.Args)>2 && os.Args[1]=="ui" && os.Args[2]=="restart" { os.Exit(4) }
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
        "Ubuntu Test|claude-code`nUbuntu Test|codex`nUbuntu Test|cline" | Set-Content "$dest/.wsl-integrations.txt" -Encoding utf8
        # Poll the log the way the wizard does (LoadStringsFromLockedFile:
        # read access, read/write sharing); appends must never collide with it.
        $ready = Join-Path $dest 'poller.ready'
        $poller = Start-Job -ArgumentList "$dest/first-run.log", $ready -ScriptBlock {
            param($log, $ready)
            New-Item -ItemType File -Path $ready | Out-Null
            while ($true) {
                try {
                    $reader = New-Object System.IO.StreamReader ([System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite'))
                    $null = $reader.ReadToEnd()
                    $reader.Dispose()
                } catch {}
            }
        }
        while (-not (Test-Path $ready)) { Start-Sleep -Milliseconds 50 }
        $child = Start-Process powershell.exe -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$dest/imprint-setup.ps1`"", '-InstallDir', "`"$dest`"", '-Profile', 'cpu'
        ) -WorkingDirectory $dest -RedirectStandardOutput "$dest/stdout.log" -RedirectStandardError "$dest/stderr.log" -PassThru
        # Windows PowerShell 5.1 needs the handle opened before the process
        # exits to retain ExitCode reliably after WaitForExit/Refresh.
        $null = $child.Handle
        if (-not $child.WaitForExit(30000 * $slack)) {
            taskkill /PID $child.Id /T /F
            Get-Content "$dest/stdout.log", "$dest/stderr.log", "$dest/first-run.log" -ErrorAction SilentlyContinue
            throw 'Setup regression fixture timed out'
        }
        Remove-Job $poller -Force
        $child.Refresh()
        $log = Get-Content "$dest/first-run.log" -Raw -Encoding UTF8
        if ($child.ExitCode -ne $fail) {
            Get-Content "$dest/stdout.log", "$dest/stderr.log"
            $log
            throw "Expected exit $fail, got $($child.ExitCode)"
        }
        if ((Get-Content "$dest/.setup-result").Trim() -ne "$fail") { throw 'Wrong setup result' }
        if ((Test-Path "$dest/.first-run.done") -ne ($fail -eq 0)) { throw 'Wrong completion sentinel' }
        if ($log -notmatch 'ordinary progress on stderr') { throw 'Native stderr was not retained' }
        if (-not $log.Contains("$([char]0x2192) utf-8 progress")) { throw 'Native UTF-8 output was not decoded as UTF-8' }
        if ($log.Contains([string][char]27)) { throw 'ANSI escapes reached the log' }
        if ($fail -eq 0) {
            $calls = @(Get-Content $env:IMPRINT_SETUP_TEST_ARGS | ForEach-Object { ,(ConvertFrom-Json $_) })
            $wslCalls = @($calls | Where-Object { $_[0] -eq 'wsl' })
            if ($wslCalls.Count -ne 3) { throw 'Saved WSL selections were not replayed' }
            # The CLI's client table, not this script, decides which targets exist.
            if ($wslCalls[0][3] -cne 'Ubuntu Test' -or $wslCalls[1][5] -cne 'codex' -or $wslCalls[2][5] -cne 'cline') {
                throw 'Saved WSL distribution/target arguments were not preserved'
            }
            if (($calls[-1] -join ' ') -cne 'ui restart') { throw 'Setup did not end by restarting the dashboard' }
            if ($log -notmatch 'Dashboard restart failed \(exit=4\)') { throw 'A failed dashboard restart was not logged' }
        }
    }
    Write-Host 'Windows PowerShell setup stderr, nonzero-exit and log regressions passed'
} finally {
    Get-Job | Remove-Job -Force
    Remove-Item Env:IMPRINT_SETUP_TEST_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:IMPRINT_SETUP_TEST_ARGS -ErrorAction SilentlyContinue
    # Windows releases a killed process's executable a moment later; a
    # leftover temp folder must not replace the error that got us here.
    foreach ($attempt in 1..10) {
        Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $root)) { break }
        Start-Sleep -Milliseconds 500
    }
}
