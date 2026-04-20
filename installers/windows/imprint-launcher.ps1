# Shortcut target for the Windows installer.
# Responsibility: start Imprint's services and open the web UI. That's it.
#
# First-run setup (venv, pip install, `imprint setup`) runs at install time
# via the Inno Setup [Run] section. The launcher only falls back to
# bootstrap if the sentinel is missing (corrupted state / manual extract).

$ErrorActionPreference = "SilentlyContinue"

$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sentinel   = Join-Path $InstallDir ".first-run.done"
$VenvDir    = Join-Path $InstallDir ".venv"
$Bin        = Join-Path $InstallDir "bin\imprint.exe"
$Reqs       = Join-Path $InstallDir "requirements.txt"
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

Most common cause: Python 3.10–3.13 is not installed or not on PATH.
Download: https://www.python.org/downloads/
Be sure to check 'Add python.exe to PATH' during install.
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

function Invoke-Bootstrap {
    $py = Find-Python
    if (-not $py) { return $false }
    try {
        if (-not (Test-Path $VenvDir)) {
            & $py.Cmd @($py.ArgsPrefix + @('-m', 'venv', $VenvDir)) | Out-Null
            if ($LASTEXITCODE -ne 0) { return $false }
        }
        & "$VenvDir\Scripts\pip.exe" install --disable-pip-version-check -q -r $Reqs | Out-Null
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
