# Invoked once from the Inno Setup [Run] section right after files are copied.
# Runs in the user's context, not elevated. Creates the Python venv, installs
# dependencies, registers Imprint with Claude Code, and drops the sentinel
# that tells the launcher "first-run is done".
#
# Runs in a VISIBLE PowerShell window (Inno [Run] without /runhidden) so the
# user can watch progress and see any errors as they happen. On failure the
# window pauses for input before closing.

param(
    [Parameter(Mandatory=$true)] [string]$InstallDir,
    [switch]$PauseOnFinish,
    [switch]$Interactive
)

$ErrorActionPreference = "Continue"
$InformationPreference = "Continue"

$VenvDir  = Join-Path $InstallDir ".venv"
$Bin      = Join-Path $InstallDir "bin\imprint.exe"
$Reqs     = Join-Path $InstallDir "requirements.txt"
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

function Find-Python {
    foreach ($c in @('python', 'py', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $argList = if ($c -eq 'py') { @('-3', '--version') } else { @('--version') }
        try {
            $out = & $cmd.Source @argList 2>&1
            if ($out -match 'Python 3\.(1[0-3])\b') {
                return @{ Cmd = $cmd.Source; ArgsPrefix = $(if ($c -eq 'py') { @('-3') } else { @() }) }
            }
        } catch { continue }
    }
    return $null
}

"" | Out-File -FilePath $Log -Encoding ascii  # truncate

Write-Host ""
Write-Host "=== Imprint first-run setup ===" -ForegroundColor Cyan
Write-Host ""
Log-Line "InstallDir = $InstallDir"

$py = Find-Python
if (-not $py) {
    Log-Line "Python 3.10–3.13 not found on PATH."
    Log-Line ""
    Log-Line "Install Python 3.13 from https://www.python.org/downloads/"
    Log-Line "and be sure to check 'Add python.exe to PATH' during install."
    Log-Line "Then re-run setup from the Start Menu: 'Imprint -> Repair Imprint'."
    Fail-Exit "Python prerequisite missing"
}
Log-Line "Python: $($py.Cmd)"

if (-not (Test-Path $VenvDir)) {
    Log-Line "Creating Python virtual environment..."
    & $py.Cmd @($py.ArgsPrefix + @('-m', 'venv', $VenvDir)) *>> $Log
    if ($LASTEXITCODE -ne 0) { Fail-Exit "venv creation failed (exit=$LASTEXITCODE)" }
}

Log-Line "Installing Python dependencies (this may take ~1 min)..."
& "$VenvDir\Scripts\pip.exe" install --disable-pip-version-check -r $Reqs *>> $Log
if ($LASTEXITCODE -ne 0) { Fail-Exit "pip install failed (exit=$LASTEXITCODE)" }

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
