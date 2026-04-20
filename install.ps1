# One-line installer for Imprint Memory Layer
#
# Usage:
#   irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex
#
# Pin to a specific release (env var):
#   $env:IMPRINT_VERSION="v0.2.0"; irm .../install.ps1 | iex
#
# Latest dev (prerelease):
#   $env:IMPRINT_CHANNEL="dev"; irm .../install.ps1 | iex
#
# Direct invocation supports args:
#   .\install.ps1 -Version v0.2.0
#   .\install.ps1 -Dev

param(
    [string]$Version = $env:IMPRINT_VERSION,
    [switch]$Dev,
    [switch]$Stable
)

$ErrorActionPreference = "Stop"

$Repo = "alexandruleca/imprint-memory-layer"
$InstallDir = Join-Path $env:USERPROFILE ".local\share\imprint"
$BinDir = Join-Path $env:USERPROFILE ".local\bin"

function Write-Info    { param($Msg) Write-Host "[*] $Msg" -ForegroundColor Cyan }
function Write-Success { param($Msg) Write-Host "[+] $Msg" -ForegroundColor Green }
function Write-Warn    { param($Msg) Write-Host "[!] $Msg" -ForegroundColor Yellow }
function Write-Fail    { param($Msg) Write-Host "[x] $Msg" -ForegroundColor Red; exit 1 }

# --- Resolve target tag ---
$Channel = $env:IMPRINT_CHANNEL
if ($Dev)    { $Channel = "dev" }
if ($Stable) { $Channel = "stable" }

function Resolve-LatestDev {
    try {
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases?per_page=30" -UseBasicParsing
        $prerelease = $releases | Where-Object { $_.prerelease -eq $true } | Select-Object -First 1
        if ($prerelease) { return $prerelease.tag_name }
    } catch {
        Write-Fail "Could not query GitHub API for dev release: $_"
    }
    return $null
}

if ($Version) {
    $TargetTag = $Version
    Write-Info "Pinned release: $TargetTag"
} elseif ($Channel -eq "dev") {
    $TargetTag = Resolve-LatestDev
    if (-not $TargetTag) { Write-Fail "Could not resolve latest dev release from GitHub API" }
    Write-Info "Latest dev release: $TargetTag"
} else {
    $TargetTag = $null
    Write-Info "Channel: stable (latest)"
}

# Release archive URL
$ArchiveName = "imprint-windows-amd64.zip"
if ($TargetTag) {
    $ArchiveUrl = "https://github.com/$Repo/releases/download/$TargetTag/$ArchiveName"
} else {
    $ArchiveUrl = "https://github.com/$Repo/releases/latest/download/$ArchiveName"
}

# --- Check prerequisites ---
# Claude Code CLI is no longer required up-front: `imprint setup all` probes
# every supported host (Claude Code, Cursor, Codex, Copilot, Cline, OpenClaw,
# Claude/ChatGPT Desktop) and skips whichever aren't installed. Warn but
# don't bail — the user can install Claude Code later and run
# `imprint setup claude-code` to register on demand.
try {
    $null = Get-Command claude -ErrorAction Stop
} catch {
    Write-Warn "Claude Code CLI not found. Imprint will install and register with any other supported host; add Claude Code later from https://docs.anthropic.com/en/docs/claude-code/overview then run 'imprint setup claude-code'."
}

# --- Download and extract release archive ---
Write-Info "Downloading release archive: $ArchiveUrl"
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("imprint-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
try {
    $Zip = Join-Path $Tmp $ArchiveName
    try {
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $Zip -UseBasicParsing
    } catch {
        Write-Fail "Download failed: $ArchiveUrl"
    }

    Write-Info "Extracting to $InstallDir (preserving data\ and .venv\)..."
    Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
    $Extracted = Join-Path $Tmp "imprint-windows-amd64"
    if (-not (Test-Path $Extracted)) { Write-Fail "Unexpected archive layout: $Extracted not found" }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    # Copy extracted tree over $InstallDir, skipping data\ and .venv\ so user state survives.
    Get-ChildItem -Path $Extracted -Force | ForEach-Object {
        if ($_.Name -in @("data", ".venv")) { return }
        Copy-Item -Path $_.FullName -Destination $InstallDir -Recurse -Force
    }

    $ImprintBin = Join-Path $InstallDir "bin\imprint.exe"
    if (-not (Test-Path $ImprintBin)) { Write-Fail "Binary missing after extract: $ImprintBin" }
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

Write-Success "Binary ready at $ImprintBin"

# --- Run setup ---
Write-Info "Running imprint setup..."
& $ImprintBin setup

Write-Success "Installation complete! Restart PowerShell to use the 'imprint' command."
