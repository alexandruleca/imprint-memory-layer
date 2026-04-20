# Download Astral's `uv` binary for Windows and drop it into the staged
# release tree at $DestDir\bin\uv.exe. Called from the `fetch-uv` Makefile
# target (via `pwsh`) before the Windows release archive is zipped.
#
# Usage:
#   pwsh -File scripts/fetch-uv.ps1 -Arch amd64 -DestDir dist/imprint-windows-amd64
#
# Pin $UvVersion deliberately per Imprint release — never track `latest`.

param(
    [Parameter(Mandatory=$true)] [ValidateSet("amd64","arm64")] [string]$Arch,
    [Parameter(Mandatory=$true)] [string]$DestDir,
    [string]$UvVersion = "0.5.11"
)

$ErrorActionPreference = "Stop"

switch ($Arch) {
    "amd64" { $Target = "x86_64-pc-windows-msvc" }
    "arm64" { $Target = "aarch64-pc-windows-msvc" }
}
$Asset   = "uv-$Target.zip"
$Url     = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$Asset"

Write-Host "[*] fetching uv $UvVersion for windows-$Arch -> $Url"

$Tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "uv-fetch-$((New-Guid).Guid)") -Force
try {
    $ZipPath = Join-Path $Tmp $Asset
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing

    Expand-Archive -Path $ZipPath -DestinationPath $Tmp -Force

    $binDir = Join-Path $DestDir "bin"
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $destBin = Join-Path $binDir "uv.exe"

    $nested = Join-Path $Tmp "uv-$Target\uv.exe"
    $flat   = Join-Path $Tmp "uv.exe"
    if (Test-Path $nested) {
        Copy-Item $nested $destBin -Force
    } elseif (Test-Path $flat) {
        Copy-Item $flat $destBin -Force
    } else {
        throw "uv.exe not found in extracted archive at $Tmp"
    }

    $size = (Get-Item $destBin).Length
    Write-Host "[+] $destBin ($size bytes)"

    # Fetch uv's own LICENSE texts — required for Apache 2.0 compliance
    # since we redistribute the binary. Dual-licensed Apache-2.0 OR MIT; we
    # ship both and let the downstream consumer pick.
    $LicDir = Join-Path $DestDir "licenses\uv"
    New-Item -ItemType Directory -Path $LicDir -Force | Out-Null
    foreach ($lic in @("LICENSE-APACHE","LICENSE-MIT")) {
        $licUrl = "https://raw.githubusercontent.com/astral-sh/uv/$UvVersion/$lic"
        try {
            Invoke-WebRequest -Uri $licUrl -OutFile (Join-Path $LicDir $lic) -UseBasicParsing
        } catch {
            Write-Warning "failed to fetch $lic (non-fatal): $_"
        }
    }
    @"
# uv $UvVersion

Astral's ``uv`` binary ships at ``bin\uv.exe``. Dual-licensed Apache-2.0
**or** MIT at the recipient's choice — full texts in this directory.

Upstream: https://github.com/astral-sh/uv
"@ | Set-Content (Join-Path $LicDir "README.md") -Encoding utf8
    Write-Host "[+] $LicDir\ (uv license texts)"
} finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
