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

function Show-Error {
    param([string]$Title, [string]$Message)
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Message, "Imprint - $Title", 'OK', 'Error') | Out-Null
}

function Find-Python {
    foreach ($c in @('python', 'py', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $argList = if ($c -eq 'py') { @('-3', '--version') } else { @('--version') }
        try {
            $out = & $cmd.Source @argList 2>&1
            if ($out -match 'Python 3\.(9|1[0-9]|[2-9][0-9])') {
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

# Fallback bootstrap if installer-time setup didn't complete.
if (-not (Test-Path $Sentinel)) {
    if (-not (Invoke-Bootstrap)) {
        Show-Error "Setup required" "Imprint could not finish first-time setup automatically. Open PowerShell in '$InstallDir' and run:`n`n  .\bin\imprint.exe setup`n`nThen re-launch Imprint."
        exit 1
    }
}

# `imprint ui open` starts Qdrant + UI daemon and opens the default browser.
Start-Process -FilePath $Bin -ArgumentList "ui", "open" -WindowStyle Hidden
