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
    foreach ($fail in @(0, 1)) {
        $env:IMPRINT_UNINSTALL_TEST_FAIL = "$fail"
        $env:IMPRINT_UNINSTALL_TEST_ARGS = "$root/calls.json"
        $child = Start-Process powershell.exe -ArgumentList @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
            "`"$root/imprint-uninstall.ps1`"", '-InstallDir', "`"$root`""
        ) -WorkingDirectory $root -PassThru -WindowStyle Hidden
        $null = $child.Handle
        if (-not $child.WaitForExit(30000)) { taskkill /PID $child.Id /T /F; throw 'Cleanup timed out' }
        $child.Refresh()
        if ($child.ExitCode -ne $fail) { throw "Expected exit $fail, got $($child.ExitCode)" }
        $calls = @(Get-Content "$root/calls.json" -Raw | ConvertFrom-Json)
        if ($calls.Count -ne 1 -or $calls[0] -ne 'disable') { throw 'Uninstaller did not use disable' }
        if ((Get-Content "$root/uninstall-cleanup.log" -Raw) -notmatch 'ordinary cleanup diagnostics') { throw 'Missing diagnostic log' }
        if ((Get-Content "$root/memories.txt").Trim() -ne 'preserve memories') { throw 'Memories changed' }
    }
    Remove-Item "$root/bin/imprint.exe"
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$root/imprint-uninstall.ps1" -InstallDir $root
    if ($LASTEXITCODE -eq 0) { throw 'Missing CLI must block uninstall' }
    Write-Host 'Windows uninstall cleanup regression passed'
} finally {
    Remove-Item Env:IMPRINT_UNINSTALL_TEST_FAIL, Env:IMPRINT_UNINSTALL_TEST_ARGS -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
