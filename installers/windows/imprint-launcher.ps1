# Shortcut target for the Windows installer.
# Responsibility: start Imprint's services and open the web UI. That's it.
#
# First-run setup (uv-provisioned venv + selected profile deps + MCP
# registration) runs at install time via the Inno Setup [Run] section. The
# launcher only falls back to bootstrap if the sentinel is missing
# (corrupted state / manual extract).

$ErrorActionPreference = "SilentlyContinue"

$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sentinel   = Join-Path $InstallDir ".first-run.done"
$Bin        = Join-Path $InstallDir "bin\imprint.exe"
$Uv         = Join-Path $InstallDir "bin\uv.exe"
$Log        = Join-Path $InstallDir "first-run.log"
$SetupScript = Join-Path $InstallDir "imprint-setup.ps1"

function Show-Error {
    param([string]$Title, [string]$Message)
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Message, "Imprint - $Title", 'OK', 'Error') | Out-Null
}

function Show-SetupError {
    # Presents the user with: actionable instructions + a Yes/No offering to
    # open the log file + a Retry button that re-runs setup in a visible
    # console window.
    Add-Type -AssemblyName PresentationFramework
    $tail = ""
    if (Test-Path $Log) {
        try {
            $tail = (Get-Content $Log -Tail 12 -ErrorAction SilentlyContinue) -join "`n"
        } catch { $tail = "" }
    }
    $body = @"
Imprint's first-run setup did not complete.

What to do next:
  1. Click Yes below to re-run setup in a visible console window
     (this is the easiest fix for most failures).
  2. If setup keeps failing, check the log at:
       $Log
     and share the last ~20 lines when asking for help.

Imprint ships its own Python via the bundled uv binary, so you do not
need to install Python yourself. If setup still fails, the most likely
causes are: no internet connection (uv needs to download Python on first
run) or a broken download (re-run the installer).
"@
    if ($tail) {
        $body += "`n`nLast log lines:`n$tail"
    }
    $choice = [System.Windows.MessageBox]::Show(
        $body,
        "Imprint - Setup required",
        [System.Windows.MessageBoxButton]::YesNoCancel,
        [System.Windows.MessageBoxImage]::Warning
    )
    switch ($choice) {
        'Yes' {
            if (Test-Path $SetupScript) {
                Start-Process powershell.exe -ArgumentList @(
                    '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-NoExit', '-File', $SetupScript,
                    '-InstallDir', $InstallDir, '-Interactive'
                )
            } else {
                Start-Process powershell.exe -ArgumentList @(
                    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit',
                    '-Command', "cd '$InstallDir'; & '$Bin' setup"
                )
            }
        }
        'No' {
            if (Test-Path $Log) {
                Start-Process notepad.exe $Log
            } else {
                Start-Process explorer.exe $InstallDir
            }
        }
    }
}

function Invoke-Bootstrap {
    # Silent fallback path. Defers to `imprint bootstrap` (uv + profile.json
    # if present). We pass --non-interactive and leave --profile unset so
    # the Go side falls back to the persisted choice, or CPU default on
    # fresh installs.
    if (-not (Test-Path $Uv)) { return $false }
    if (-not (Test-Path $Bin)) { return $false }
    try {
        & $Bin bootstrap --non-interactive | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        & $Bin setup | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        New-Item -ItemType File -Path $Sentinel -Force | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path $Bin)) {
    Show-Error "Missing binary" "Imprint binary not found at $Bin. Reinstall from https://imprintmcp.alexandruleca.com/download."
    exit 1
}

# Fallback bootstrap if installer-time setup didn't complete. Attempt it
# silently once; if that fails too, surface the rich Show-SetupError dialog
# so the user can retry interactively or inspect the log.
if (-not (Test-Path $Sentinel)) {
    if (-not (Invoke-Bootstrap)) {
        Show-SetupError
        exit 1
    }
}

# `imprint ui open` starts Qdrant + UI daemon and opens the default browser.
Start-Process -FilePath $Bin -ArgumentList "ui", "open" -WindowStyle Hidden
