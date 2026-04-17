#!/usr/bin/env bash
set -euo pipefail

# One-line installer for Imprint Memory Layer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
#
# Pin to a specific release:
#   IMPRINT_VERSION=v0.2.0 curl -fsSL .../install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version v0.2.0
#
# Install latest dev (prerelease):
#   IMPRINT_CHANNEL=dev curl -fsSL .../install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --dev

REPO="alexandruleca/imprint-memory-layer"
INSTALL_DIR="$HOME/.local/share/imprint"
BIN_DIR="$HOME/.local/bin"

# --- Parse args ---
VERSION="${IMPRINT_VERSION:-}"
CHANNEL="${IMPRINT_CHANNEL:-stable}"
ASSUME_YES="${IMPRINT_ASSUME_YES:-0}"
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --version=*) VERSION="${1#--version=}"; shift ;;
        --dev) CHANNEL="dev"; shift ;;
        --stable) CHANNEL="stable"; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        -h|--help)
            sed -n '3,16p' "$0" 2>/dev/null || echo "See script header for usage."
            exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# --- Output helpers ---
info()    { printf "\033[0;36m[*]\033[0m %s\n" "$1"; }
success() { printf "\033[0;32m[+]\033[0m %s\n" "$1"; }
fail()    { printf "\033[0;31m[x]\033[0m %s\n" "$1"; exit 1; }

# --- Detect OS and arch ---
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
    linux)  PLATFORM="linux" ;;
    darwin) PLATFORM="darwin" ;;
    *)      fail "Unsupported OS: $OS. Use install.ps1 on Windows." ;;
esac

case "$ARCH" in
    x86_64|amd64)  ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *)             fail "Unsupported architecture: $ARCH" ;;
esac

info "Detected platform: ${PLATFORM}/${ARCH}"

# --- Resolve target tag ---
resolve_latest_dev() {
    command -v python3 >/dev/null || fail "--dev requires python3 for GitHub API parse"
    command -v curl >/dev/null || fail "--dev requires curl"
    curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=30" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print(next((x["tag_name"] for x in r if x.get("prerelease")), ""))'
}

if [ -n "$VERSION" ]; then
    TARGET_TAG="$VERSION"
    info "Pinned release: $TARGET_TAG"
elif [ "$CHANNEL" = "dev" ]; then
    TARGET_TAG="$(resolve_latest_dev)"
    [ -n "$TARGET_TAG" ] || fail "Could not resolve latest dev release from GitHub API"
    info "Latest dev release: $TARGET_TAG"
else
    TARGET_TAG=""   # empty → use /releases/latest (latest stable, auto-resolved by GitHub)
    info "Channel: stable (latest)"
fi

# Release archive URL for this platform
ARCHIVE_NAME="imprint-${PLATFORM}-${ARCH}.tar.gz"
if [ -n "$TARGET_TAG" ]; then
    ARCHIVE_URL="https://github.com/$REPO/releases/download/${TARGET_TAG}/${ARCHIVE_NAME}"
else
    ARCHIVE_URL="https://github.com/$REPO/releases/latest/download/${ARCHIVE_NAME}"
fi

# --- Check prerequisites ---
if ! command -v claude &>/dev/null; then
    fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
fi
command -v tar &>/dev/null || fail "tar is required but not found"
command -v rsync &>/dev/null || fail "rsync is required but not found"
DOWNLOADER=""
if command -v curl &>/dev/null; then DOWNLOADER="curl"
elif command -v wget &>/dev/null; then DOWNLOADER="wget"
else fail "Neither curl nor wget found"
fi

# --- Download and extract release archive ---
info "Downloading release archive: $ARCHIVE_URL"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARBALL="$TMP/$ARCHIVE_NAME"
if [ "$DOWNLOADER" = "curl" ]; then
    curl -fsSL "$ARCHIVE_URL" -o "$TARBALL" || fail "Download failed: $ARCHIVE_URL"
else
    wget -q "$ARCHIVE_URL" -O "$TARBALL" || fail "Download failed: $ARCHIVE_URL"
fi

# Existing install? Warn and require an explicit yes before overwriting the
# source tree (data/ and .venv/ are still preserved regardless). When stdin
# is piped from curl, require IMPRINT_ASSUME_YES=1 or `-s -- --yes`.
EXISTING_INSTALL=0
if [ -f "$INSTALL_DIR/bin/imprint" ]; then
    EXISTING_INSTALL=1
    info "Existing install detected at $INSTALL_DIR"
    info "Will replace code; preserves data/ (workspaces, qdrant, sqlite, config, gpu_state) and .venv/"
    if [ "$ASSUME_YES" != "1" ]; then
        if [ -t 0 ]; then
            printf "\033[0;36m[?]\033[0m Upgrade existing install? [y/N] "
            read -r REPLY
            case "$REPLY" in
                y|Y|yes|YES) ;;
                *) info "Aborted."; exit 0 ;;
            esac
        else
            fail "stdin is not a TTY — re-run with IMPRINT_ASSUME_YES=1 or 'bash -s -- --yes' to confirm upgrade"
        fi
    fi
fi

info "Extracting to $INSTALL_DIR (preserving data/ and .venv/)..."
tar -xzf "$TARBALL" -C "$TMP" || fail "Failed to extract $TARBALL"
EXTRACTED="$TMP/imprint-${PLATFORM}-${ARCH}"
[ -d "$EXTRACTED" ] || fail "Unexpected archive layout: $EXTRACTED not found"
mkdir -p "$INSTALL_DIR"
# rsync over-top: upgrades code files, leaves data/ and .venv/ alone.
# --delete-during drops stale files from the previous release (e.g. renamed
# scripts) but never descends into the excluded dirs.
RSYNC_FLAGS="-a --exclude data/ --exclude .venv/"
if [ "$EXISTING_INSTALL" = "1" ]; then
    RSYNC_FLAGS="$RSYNC_FLAGS --delete-during"
fi
rsync $RSYNC_FLAGS "$EXTRACTED/" "$INSTALL_DIR/"

IMPRINT_BIN="$INSTALL_DIR/bin/imprint"
[ -f "$IMPRINT_BIN" ] || fail "Binary missing after extract: $IMPRINT_BIN"
chmod +x "$IMPRINT_BIN"
success "Binary ready at $IMPRINT_BIN"

# --- Set up Python venv and dependencies ---
info "Setting up Python virtual environment..."

PYTHON=""
for cmd in python3 python; do
    if $cmd --version 2>/dev/null | grep -qE 'Python 3\.(9|1[0-9]|[2-9][0-9])'; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.9+ not found. Install Python first."
fi

VENV_DIR="$INSTALL_DIR/.venv"
if [ -d "$VENV_DIR" ] && "$VENV_DIR/bin/python" --version &>/dev/null; then
    info "Virtual environment already exists at $VENV_DIR"
else
    rm -rf "$VENV_DIR"
    $PYTHON -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
    success "Created virtual environment at $VENV_DIR"
fi

info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet || fail "Failed to install dependencies"
success "Python dependencies installed"

# --- Symlink into PATH ---
info "Linking imprint into $BIN_DIR..."
mkdir -p "$BIN_DIR"
ln -sf "$IMPRINT_BIN" "$BIN_DIR/imprint"
success "Symlinked $BIN_DIR/imprint → $IMPRINT_BIN"

# --- Run setup ---
info "Running imprint setup..."
"$IMPRINT_BIN" setup

if [ "$EXISTING_INSTALL" = "1" ]; then
    success "Update complete. Preserved: data/ (workspaces, qdrant, sqlite, config, gpu_state.json), .venv/"
    info "Future updates can use 'imprint update' directly — no curl required."
else
    success "Installation complete! Restart your terminal to use the 'imprint' command."
fi
