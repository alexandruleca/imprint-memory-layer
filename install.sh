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
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --version=*) VERSION="${1#--version=}"; shift ;;
        --dev) CHANNEL="dev"; shift ;;
        --stable) CHANNEL="stable"; shift ;;
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

# Release asset URL for this platform
if [ -n "$TARGET_TAG" ]; then
    RELEASE_URL="https://github.com/$REPO/releases/download/${TARGET_TAG}/imprint-${PLATFORM}-${ARCH}"
else
    RELEASE_URL="https://github.com/$REPO/releases/latest/download/imprint-${PLATFORM}-${ARCH}"
fi

# --- Check prerequisites ---
if ! command -v claude &>/dev/null; then
    fail "Claude Code CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code/overview"
fi

# --- Clone or update repo ---
info "Setting up imprint repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" fetch --tags --quiet origin 2>/dev/null || true
    if [ -n "$TARGET_TAG" ]; then
        git -C "$INSTALL_DIR" checkout --quiet "$TARGET_TAG" 2>/dev/null \
            || fail "Could not checkout tag $TARGET_TAG in $INSTALL_DIR"
    else
        git -C "$INSTALL_DIR" checkout --quiet main 2>/dev/null || true
        git -C "$INSTALL_DIR" pull --quiet 2>/dev/null || true
    fi
else
    rm -rf "$INSTALL_DIR"
    if [ -n "$TARGET_TAG" ]; then
        git clone --quiet --branch "$TARGET_TAG" --depth 1 "https://github.com/$REPO.git" "$INSTALL_DIR" \
            || fail "Failed to clone at tag $TARGET_TAG"
    else
        git clone --quiet "https://github.com/$REPO.git" "$INSTALL_DIR"
    fi
fi

# --- Acquire binary: bundled → build → download ---
BUNDLED_BIN="$INSTALL_DIR/bin/imprint-${PLATFORM}-${ARCH}"
if [ -f "$BUNDLED_BIN" ]; then
    IMPRINT_BIN="$BUNDLED_BIN"
    chmod +x "$IMPRINT_BIN"
    info "Using bundled binary: $IMPRINT_BIN"
elif command -v go &>/dev/null; then
    info "Bundled binary not found for ${PLATFORM}/${ARCH}. Go found — building from source..."
    cd "$INSTALL_DIR"
    LDVER="${TARGET_TAG:-dev}"
    go build -ldflags "-s -w -X main.version=${LDVER}" -o build/imprint . 2>/dev/null
    IMPRINT_BIN="$INSTALL_DIR/build/imprint"
else
    info "Downloading pre-built binary from: $RELEASE_URL"
    mkdir -p "$INSTALL_DIR/build"
    IMPRINT_BIN="$INSTALL_DIR/build/imprint"
    if command -v curl &>/dev/null; then
        curl -fsSL "$RELEASE_URL" -o "$IMPRINT_BIN" 2>/dev/null || fail "Download failed: $RELEASE_URL"
    elif command -v wget &>/dev/null; then
        wget -q "$RELEASE_URL" -O "$IMPRINT_BIN" 2>/dev/null || fail "Download failed: $RELEASE_URL"
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
