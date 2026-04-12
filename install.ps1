# One-line installer for Knowledge (MemPalace CLI)
# Usage: irm https://raw.githubusercontent.com/alexandruleca/claude-code-memory-layer/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$Repo = "alexandruleca/claude-code-memory-layer"
$InstallDir = Join-Path $env:USERPROFILE ".local\share\knowledge"
$BinDir = Join-Path $env:USERPROFILE ".local\bin"

function Write-Info    { param($Msg) Write-Host "[*] $Msg" -ForegroundColor Cyan }
function Write-Success { param($Msg) Write-Host "[+] $Msg" -ForegroundColor Green }
function Write-Fail    { param($Msg) Write-Host "[x] $Msg" -ForegroundColor Red; exit 1 }

# --- Check prerequisites ---
try {
    $null = Get-Command claude -ErrorAction Stop
} catch {
    Write-Fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
}

# --- Clone or update repo ---
Write-Info "Setting up knowledge repository..."
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Updating existing installation..."
    git -C $InstallDir pull --quiet 2>$null
} else {
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    git clone --quiet "https://github.com/$Repo.git" $InstallDir
}

# --- Build or download binary ---
$KnowledgeBin = Join-Path $InstallDir "build\knowledge.exe"

try {
    $null = Get-Command go -ErrorAction Stop
    Write-Info "Go found - building from source..."
    Push-Location $InstallDir
    go build -ldflags "-s -w" -o "build\knowledge.exe" . 2>$null
    Pop-Location
} catch {
    Write-Info "Downloading pre-built binary..."
    $ReleaseUrl = "https://github.com/$Repo/releases/latest/download/knowledge-windows-amd64.exe"
    New-Item -ItemType Directory -Path (Join-Path $InstallDir "build") -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $ReleaseUrl -OutFile $KnowledgeBin -UseBasicParsing
    } catch {
        Write-Fail "Download failed. Install Go and run again to build from source."
    }
}

Write-Success "Binary ready at $KnowledgeBin"

# --- Run setup ---
Write-Info "Running knowledge setup..."
& $KnowledgeBin setup

Write-Success "Installation complete! Restart PowerShell to use 'knowledge' and 'mempalace' commands."
