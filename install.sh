#!/usr/bin/env bash
set -euo pipefail

# One-line installer for Imprint Memory Layer
# Usage: curl -fsSL https://raw.githubusercontent.com/alexandruleca/claude-code-memory-layer/main/install.sh | bash

REPO="alexandruleca/claude-code-memory-layer"
INSTALL_DIR="$HOME/.local/share/imprint"
BIN_DIR="$HOME/.local/bin"

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

BINARY="imprint"
RELEASE_URL="https://github.com/$REPO/releases/latest/download/imprint-${PLATFORM}-${ARCH}"

info "Detected platform: ${PLATFORM}/${ARCH}"

# --- Check prerequisites ---
if ! command -v claude &>/dev/null; then
    fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
fi

# --- Clone or update repo ---
info "Setting up imprint repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --quiet 2>/dev/null || true
else
    rm -rf "$INSTALL_DIR"
    git clone --quiet "https://github.com/$REPO.git" "$INSTALL_DIR"
fi

# --- Use pre-built binary from bin/ ---
BUNDLED_BIN="$INSTALL_DIR/bin/imprint-${PLATFORM}-${ARCH}"
if [ -f "$BUNDLED_BIN" ]; then
    IMPRINT_BIN="$BUNDLED_BIN"
    chmod +x "$IMPRINT_BIN"
    info "Using bundled binary: $IMPRINT_BIN"
elif command -v go &>/dev/null; then
    info "Bundled binary not found for ${PLATFORM}/${ARCH}. Go found — building from source..."
    cd "$INSTALL_DIR"
    go build -ldflags "-s -w" -o build/imprint . 2>/dev/null
    IMPRINT_BIN="$INSTALL_DIR/build/imprint"
else
    info "Downloading pre-built binary..."
    mkdir -p "$INSTALL_DIR/build"
    IMPRINT_BIN="$INSTALL_DIR/build/imprint"
    if command -v curl &>/dev/null; then
        curl -fsSL "$RELEASE_URL" -o "$IMPRINT_BIN" 2>/dev/null || fail "Download failed. Install Go and run again to build from source."
    elif command -v wget &>/dev/null; then
        wget -q "$RELEASE_URL" -O "$IMPRINT_BIN" 2>/dev/null || fail "Download failed. Install Go and run again to build from source."
    else
        fail "Neither curl nor wget found. Install Go and run again to build from source."
    fi
    chmod +x "$IMPRINT_BIN"
fi

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

success "Installation complete! Restart your terminal to use the 'imprint' command."
