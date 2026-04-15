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

# Release asset URL
if ($TargetTag) {
    $ReleaseUrl = "https://github.com/$Repo/releases/download/$TargetTag/imprint-windows-amd64.exe"
} else {
    $ReleaseUrl = "https://github.com/$Repo/releases/latest/download/imprint-windows-amd64.exe"
}

# --- Check prerequisites ---
try {
    $null = Get-Command claude -ErrorAction Stop
} catch {
    Write-Fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
}

# --- Clone or update repo ---
Write-Info "Setting up imprint repository..."
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Updating existing installation..."
    git -C $InstallDir fetch --tags --quiet origin 2>$null
    if ($TargetTag) {
        git -C $InstallDir checkout --quiet $TargetTag 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Fail "Could not checkout tag $TargetTag" }
    } else {
        git -C $InstallDir checkout --quiet main 2>$null
        git -C $InstallDir pull --quiet 2>$null
    }
} else {
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    if ($TargetTag) {
        git clone --quiet --branch $TargetTag --depth 1 "https://github.com/$Repo.git" $InstallDir
        if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to clone at tag $TargetTag" }
    } else {
        git clone --quiet "https://github.com/$Repo.git" $InstallDir
    }
}

# --- Acquire binary: bundled → build → download ---
$BundledBin = Join-Path $InstallDir "bin\imprint-windows-amd64.exe"
$ImprintBin = Join-Path $InstallDir "build\imprint.exe"

if (Test-Path $BundledBin) {
    $ImprintBin = $BundledBin
    Write-Info "Using bundled binary: $ImprintBin"
} else {
    try {
        $null = Get-Command go -ErrorAction Stop
        Write-Info "Bundled binary not found. Go found - building from source..."
        Push-Location $InstallDir
        $LdVer = if ($TargetTag) { $TargetTag } else { "dev" }
        go build -ldflags "-s -w -X main.version=$LdVer" -o "build\imprint.exe" . 2>$null
        Pop-Location
    } catch {
        Write-Info "Downloading pre-built binary from: $ReleaseUrl"
        New-Item -ItemType Directory -Path (Join-Path $InstallDir "build") -Force | Out-Null
        try {
            Invoke-WebRequest -Uri $ReleaseUrl -OutFile $ImprintBin -UseBasicParsing
        } catch {
            Write-Fail "Download failed: $ReleaseUrl"
        }
    }
}

Write-Success "Binary ready at $ImprintBin"

# --- Run setup ---
Write-Info "Running imprint setup..."
& $ImprintBin setup

Write-Success "Installation complete! Restart PowerShell to use the 'imprint' command."
