# Invoked once from the Inno Setup [Run] section right after files are copied.
# Runs in the user's context, not elevated. Delegates venv creation + dep
# install to the bundled `uv.exe` + `imprint bootstrap` - no host Python
# required.
#
# Runs in a VISIBLE PowerShell window (Inno [Run] without /runhidden) so the
# user can watch uv download Python + wheels and see any errors as they
# happen. On failure the window pauses for input before closing.
#
# Parameters:
#   -InstallDir <path>      Imprint install root (required).
#   -Profile cpu|gpu|auto   Install profile (default: cpu).
#   -WithLlm                Install llama-cpp-python (default: off).
#   -PauseOnFinish          Linger 3s after success (visible [Run] only).
#   -Interactive            Wait for Enter after success/failure (Repair
#                           Imprint shortcut uses this).

param(
    [Parameter(Mandatory=$true)] [string]$InstallDir,
    [ValidateSet("","cpu","gpu","auto")] [string]$Profile = "",
    [switch]$WithLlm,
    [switch]$NoLlm,
    [switch]$PauseOnFinish,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$InformationPreference = "Continue"

$Bin      = Join-Path $InstallDir "bin\imprint.exe"
$Uv       = Join-Path $InstallDir "bin\uv.exe"
$Sentinel = Join-Path $InstallDir ".first-run.done"
$Log      = Join-Path $InstallDir "first-run.log"
$ResultFile = Join-Path $InstallDir ".setup-result"

trap {
    "FAILED: $_" | Out-File -FilePath $Log -Append -Encoding utf8
    Set-Content -Path $ResultFile -Value '1' -Encoding ascii
    exit 1
}

function Log-Line {
    param([string]$Msg)
    $stamped = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Msg
    $stamped | Out-File -FilePath $Log -Append -Encoding utf8
    Write-Host $stamped
}

function Fail-Exit {
    param([string]$Msg)
    Log-Line "FAILED: $Msg"
    Set-Content -Path $ResultFile -Value '1' -Encoding ascii
    Write-Host ""
    Write-Host "Log file: $Log" -ForegroundColor Yellow
    if ($PauseOnFinish -or $Interactive) {
        Write-Host ""
        Write-Host "Press Enter to close this window..." -ForegroundColor Yellow
        [void][System.Console]::ReadLine()
    }
    exit 1
}

function Invoke-Imprint {
    param([string[]]$Arguments)
    # Windows PowerShell 5.1 wraps native stderr as NativeCommandError.
    # Progress on stderr is normal; judge the native process by its exit code.
    $previousPreference = $ErrorActionPreference
    $nativeExit = $null
    try {
        $ErrorActionPreference = 'Continue'
        # Native commands update the global automatic variable. A local
        # assignment would shadow it and hide the completed process's exit.
        $global:LASTEXITCODE = $null
        & $Bin @Arguments 2>&1 | ForEach-Object {
            "$_" | Out-File -FilePath $Log -Append -Encoding utf8
        }
        $nativeExit = $global:LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $nativeExit) { throw 'Could not execute imprint.exe' }
    return [int]$nativeExit
}

"" | Out-File -FilePath $Log -Encoding utf8  # truncate
Remove-Item $ResultFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Imprint first-run setup ===" -ForegroundColor Cyan
Write-Host ""
Log-Line "InstallDir = $InstallDir"
Log-Line "Profile    = $Profile"
Log-Line "WithLlm    = $($WithLlm.IsPresent)"

if (-not (Test-Path $Uv)) {
    Fail-Exit "Bundled uv.exe not found at $Uv. Re-download the installer."
}
if (-not (Test-Path $Bin)) {
    Fail-Exit "imprint.exe not found at $Bin. Installer is broken."
}

try {
    $uvVer = & $Uv --version 2>&1
    Log-Line "uv: $uvVer"
} catch {
    Fail-Exit "uv.exe is present but not runnable: $_"
}

# Assemble flags for `imprint bootstrap` - the Go side handles venv
# provisioning via uv, Python download, and wheel install. Omit flags the
# user didn't specify so the Go side falls back to profile.json (repairs
# reuse the last chosen profile instead of overriding it).
$BootstrapArgs = @("bootstrap")
if ($Profile -ne "") { $BootstrapArgs += @("--profile", $Profile) }
if ($WithLlm) { $BootstrapArgs += "--with-llm" }
elseif ($NoLlm) { $BootstrapArgs += "--no-llm" }
$BootstrapArgs += "--non-interactive"

Log-Line "Running: imprint $($BootstrapArgs -join ' ')"
$bootstrapExit = Invoke-Imprint -Arguments $BootstrapArgs
if ($bootstrapExit -ne 0) { Fail-Exit "imprint bootstrap failed (exit=$bootstrapExit) - see $Log" }

Log-Line "Registering Imprint with detected Windows clients..."
$setupExit = Invoke-Imprint -Arguments @('setup', '--non-interactive')
if ($setupExit -ne 0) { Fail-Exit "imprint setup failed (exit=$setupExit)" }

# Explicit selections made in the wizard are retained for repair and silent
# updates. Fresh silent installs never edit WSL profiles implicitly.
$WslSelections = Join-Path $InstallDir '.wsl-integrations.txt'
if (Test-Path $WslSelections) {
    foreach ($selection in Get-Content $WslSelections -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($selection)) { continue }
        $parts = $selection.Split('|')
        if ($parts.Count -ne 2 -or $parts[1] -notin @('claude-code', 'codex')) {
            Fail-Exit 'Invalid saved WSL integration selection'
        }
        Log-Line "Registering $($parts[1]) in WSL $($parts[0])..."
        $wslExit = Invoke-Imprint -Arguments @('wsl', 'setup', '--distribution', $parts[0], '--target', $parts[1])
        if ($wslExit -ne 0) { Fail-Exit "WSL registration failed (exit=$wslExit). Check that the selected distribution and client profile still exist." }
    }
}

New-Item -ItemType File -Path $Sentinel -Force | Out-Null
Log-Line "Setup complete."
Set-Content -Path $ResultFile -Value '0' -Encoding ascii

if ($Interactive) {
    Write-Host ""
    Write-Host "[+] Setup complete. You can close this window and launch Imprint." -ForegroundColor Green
    Write-Host "Press Enter to close..." -ForegroundColor Yellow
    [void][System.Console]::ReadLine()
} elseif ($PauseOnFinish) {
    Write-Host ""
    Write-Host "Done. This window will close in 3 seconds..." -ForegroundColor Green
    Start-Sleep -Seconds 3
}
exit 0
