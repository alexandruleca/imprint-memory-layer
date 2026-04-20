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

$ErrorActionPreference = "Continue"
$InformationPreference = "Continue"

$Bin      = Join-Path $InstallDir "bin\imprint.exe"
$Uv       = Join-Path $InstallDir "bin\uv.exe"
$Sentinel = Join-Path $InstallDir ".first-run.done"
$Log      = Join-Path $InstallDir "first-run.log"

function Log-Line {
    param([string]$Msg)
    $stamped = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Msg
    $stamped | Out-File -FilePath $Log -Append -Encoding ascii
    Write-Host $stamped
}

function Fail-Exit {
    param([string]$Msg)
    Log-Line "FAILED: $Msg"
    Write-Host ""
    Write-Host "Log file: $Log" -ForegroundColor Yellow
    if ($PauseOnFinish -or $Interactive) {
        Write-Host ""
        Write-Host "Press Enter to close this window..." -ForegroundColor Yellow
        [void][System.Console]::ReadLine()
    }
    exit 1
}

"" | Out-File -FilePath $Log -Encoding ascii  # truncate

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
& $Bin @BootstrapArgs *>> $Log
if ($LASTEXITCODE -ne 0) { Fail-Exit "imprint bootstrap failed (exit=$LASTEXITCODE) - see $Log" }

Log-Line "Registering Imprint with Claude Code..."
& $Bin setup *>> $Log
if ($LASTEXITCODE -ne 0) { Fail-Exit "imprint setup failed (exit=$LASTEXITCODE)" }

New-Item -ItemType File -Path $Sentinel -Force | Out-Null
Log-Line "Setup complete."

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
