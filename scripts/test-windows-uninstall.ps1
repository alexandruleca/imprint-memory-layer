# Native regression: cleanup must finish before Inno is allowed to delete files.
param([string]$FixtureBinary)
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('imprint-uninstall-test-' + [guid]::NewGuid())
$helper = (Resolve-Path "$PSScriptRoot/../installers/windows/imprint-uninstall.ps1").Path
New-Item -ItemType Directory -Path "$root/bin" -Force | Out-Null
try {
    if ($FixtureBinary) { Copy-Item $FixtureBinary "$root/bin/imprint.exe" } else {
        @'
package main
import("fmt";"os";"encoding/json")
func main(){
 f,e:=os.Create(os.Getenv("IMPRINT_UNINSTALL_TEST_ARGS"));if e!=nil{os.Exit(2)}
 json.NewEncoder(f).Encode(os.Args[1:]);f.Close()
 fmt.Fprintln(os.Stderr,"ordinary cleanup diagnostics")
 if os.Getenv("IMPRINT_UNINSTALL_TEST_FAIL")=="1"{os.Exit(23)}
}
'@ | Set-Content "$root/fixture.go" -Encoding ascii
        & go build -o "$root/bin/imprint.exe" "$root/fixture.go"
        if ($LASTEXITCODE -ne 0) { throw 'Could not compile uninstall fixture' }
    }
    Copy-Item $helper "$root/imprint-uninstall.ps1"
    'preserve memories' | Set-Content "$root/memories.txt"
    $cases = @(
        @{ Fail = 0; Switches = @(); Expected = 'uninstall -y' },
        @{ Fail = 0; Switches = @('-KeepData'); Expected = 'uninstall -y --keep-data' },
        @{ Fail = 1; Switches = @(); Expected = 'uninstall -y' }
    )
    foreach ($case in $cases) {
        $fail = $case.Fail
        $env:IMPRINT_UNINSTALL_TEST_FAIL = "$fail"
        $env:IMPRINT_UNINSTALL_TEST_ARGS = "$root/calls.json"
        $child = Start-Process powershell.exe -ArgumentList (@(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
            "`"$root/imprint-uninstall.ps1`"", '-InstallDir', "`"$root`""
        ) + $case.Switches) -WorkingDirectory $root -PassThru -WindowStyle Hidden
        $null = $child.Handle
        if (-not $child.WaitForExit(30000)) { taskkill /PID $child.Id /T /F; throw 'Cleanup timed out' }
        $child.Refresh()
        if ($child.ExitCode -ne $fail) { throw "Expected exit $fail, got $($child.ExitCode)" }
        $calls = @(Get-Content "$root/calls.json" -Raw | ConvertFrom-Json)
        if (($calls -join ' ') -ne $case.Expected) { throw "Expected '$($case.Expected)', got '$($calls -join ' ')'" }
        if ((Get-Content "$root/uninstall-cleanup.log" -Raw) -notmatch 'ordinary cleanup diagnostics') { throw 'Missing diagnostic log' }
        # The helper delegates deletion to the CLI; it must not touch data itself.
        if ((Get-Content "$root/memories.txt").Trim() -ne 'preserve memories') { throw 'Memories changed' }
    }
    Remove-Item "$root/bin/imprint.exe"
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$root/imprint-uninstall.ps1" -InstallDir $root
    if ($LASTEXITCODE -eq 0) { throw 'Missing CLI must block uninstall' }
    # GitHub Actions exits with the last native code after the script returns.
    # The missing-CLI failure above is expected and has been asserted.
    $global:LASTEXITCODE = 0
    Write-Host 'Windows uninstall cleanup regression passed'
} finally {
    Remove-Item Env:IMPRINT_UNINSTALL_TEST_FAIL, Env:IMPRINT_UNINSTALL_TEST_ARGS -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
