#!/usr/bin/env bash
set -euo pipefail

# One-line installer for Knowledge (MemPalace CLI)
# Usage: curl -fsSL https://raw.githubusercontent.com/alexandruleca/claude-code-memory-layer/main/install.sh | bash

REPO="alexandruleca/claude-code-memory-layer"
INSTALL_DIR="$HOME/.local/share/knowledge"
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

BINARY="knowledge"
RELEASE_URL="https://github.com/$REPO/releases/latest/download/knowledge-${PLATFORM}-${ARCH}"

info "Detected platform: ${PLATFORM}/${ARCH}"

# --- Check prerequisites ---
if ! command -v claude &>/dev/null; then
    fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
fi

# --- Clone or update repo ---
info "Setting up knowledge repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --quiet 2>/dev/null || true
else
    rm -rf "$INSTALL_DIR"
    git clone --quiet "https://github.com/$REPO.git" "$INSTALL_DIR"
fi

# --- Build or download binary ---
if command -v go &>/dev/null; then
    info "Go found — building from source..."
    cd "$INSTALL_DIR"
    go build -ldflags "-s -w" -o build/knowledge . 2>/dev/null
    KNOWLEDGE_BIN="$INSTALL_DIR/build/knowledge"
else
    info "Downloading pre-built binary..."
    mkdir -p "$INSTALL_DIR/build"
    KNOWLEDGE_BIN="$INSTALL_DIR/build/knowledge"
    if command -v curl &>/dev/null; then
        curl -fsSL "$RELEASE_URL" -o "$KNOWLEDGE_BIN" 2>/dev/null || fail "Download failed. Install Go and run again to build from source."
    elif command -v wget &>/dev/null; then
        wget -q "$RELEASE_URL" -O "$KNOWLEDGE_BIN" 2>/dev/null || fail "Download failed. Install Go and run again to build from source."
    else
        fail "Neither curl nor wget found. Install Go and run again to build from source."
    fi
    chmod +x "$KNOWLEDGE_BIN"
fi

success "Binary ready at $KNOWLEDGE_BIN"

# --- Run setup ---
info "Running knowledge setup..."
"$KNOWLEDGE_BIN" setup

success "Installation complete! Restart your terminal to use 'knowledge' and 'mempalace' commands."
