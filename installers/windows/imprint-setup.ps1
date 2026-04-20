# Invoked once from the Inno Setup [Run] section right after files are copied.
# Runs in the user's context, not elevated. Creates the Python venv, installs
# dependencies, registers Imprint with Claude Code, and drops the sentinel
# that tells the launcher "first-run is done".
#
# Failures here are non-fatal for the installer itself — the launcher will
# retry the bootstrap on first open (with a user-facing error if it fails
# again).

param(
    [Parameter(Mandatory=$true)] [string]$InstallDir
)

$ErrorActionPreference = "Continue"

$VenvDir  = Join-Path $InstallDir ".venv"
$Bin      = Join-Path $InstallDir "bin\imprint.exe"
$Reqs     = Join-Path $InstallDir "requirements.txt"
$Sentinel = Join-Path $InstallDir ".first-run.done"
$Log      = Join-Path $InstallDir "first-run.log"

function Log-Line {
    param([string]$Msg)
    ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Msg) | Out-File -FilePath $Log -Append -Encoding ascii
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

"" | Out-File -FilePath $Log -Encoding ascii  # truncate

Log-Line "InstallDir = $InstallDir"

$py = Find-Python
if (-not $py) {
    Log-Line "Python 3.9+ not found; launcher will retry."
    exit 0
}
Log-Line "Python: $($py.Cmd)"

if (-not (Test-Path $VenvDir)) {
    Log-Line "Creating venv..."
    & $py.Cmd @($py.ArgsPrefix + @('-m', 'venv', $VenvDir)) *>> $Log
    if ($LASTEXITCODE -ne 0) { Log-Line "venv creation failed"; exit 0 }
}

Log-Line "Installing Python dependencies..."
& "$VenvDir\Scripts\pip.exe" install --disable-pip-version-check -q -r $Reqs *>> $Log
if ($LASTEXITCODE -ne 0) { Log-Line "pip install failed"; exit 0 }

Log-Line "Running imprint setup..."
& $Bin setup *>> $Log
if ($LASTEXITCODE -ne 0) { Log-Line "imprint setup failed"; exit 0 }

New-Item -ItemType File -Path $Sentinel -Force | Out-Null
Log-Line "Setup complete."
exit 0
