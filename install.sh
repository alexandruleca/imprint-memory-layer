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
#
# Profile & extras (bypass the interactive prompt):
#   curl -fsSL .../install.sh | bash -s -- --profile gpu --with-llm
#   curl -fsSL .../install.sh | bash -s -- --profile cpu --non-interactive
#
# No host Python required — the release archive ships a statically-linked
# `uv` binary (Astral) that downloads its own Python via python-build-standalone.

REPO="alexandruleca/imprint-memory-layer"
INSTALL_DIR="$HOME/.local/share/imprint"
BIN_DIR="$HOME/.local/bin"

# --- Parse args ---
VERSION="${IMPRINT_VERSION:-}"
CHANNEL="${IMPRINT_CHANNEL:-stable}"
ASSUME_YES="${IMPRINT_ASSUME_YES:-0}"
PROFILE="${IMPRINT_PROFILE:-}"
WITH_LLM="${IMPRINT_WITH_LLM:-}"
NON_INTERACTIVE="${IMPRINT_NON_INTERACTIVE:-0}"
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --version=*) VERSION="${1#--version=}"; shift ;;
        --dev) CHANNEL="dev"; shift ;;
        --stable) CHANNEL="stable"; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --profile=*) PROFILE="${1#--profile=}"; shift ;;
        --with-llm) WITH_LLM=1; shift ;;
        --no-llm) WITH_LLM=0; shift ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        -h|--help)
            sed -n '3,22p' "$0" 2>/dev/null || echo "See script header for usage."
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
    # Not fatal: `imprint setup all` probes every supported host (Claude
    # Code, Cursor, Codex, Copilot, Cline, OpenClaw, Claude/ChatGPT
    # Desktop) and skips the ones that aren't installed. User can add
    # Claude Code later then run `imprint setup claude-code`.
    info "Claude Code CLI not found on PATH — skipping. Install from https://docs.anthropic.com/en/docs/claude-code/overview and run 'imprint setup claude-code' later."
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
# rsync over-top: upgrades code files, leaves runtime state alone.
# --delete-during drops stale files from the previous release (e.g. renamed
# scripts) but never descends into the excluded dirs.
#   data/       indexed memories, configs, workspace state
#   .venv/      uv-provisioned Python venv
#   python/     uv-managed Python interpreter (UV_PYTHON_INSTALL_DIR)
#   .uv-cache/  uv wheel cache (UV_CACHE_DIR)
RSYNC_FLAGS="-a --exclude data/ --exclude .venv/ --exclude python/ --exclude .uv-cache/"
if [ "$EXISTING_INSTALL" = "1" ]; then
    RSYNC_FLAGS="$RSYNC_FLAGS --delete-during"
fi
rsync $RSYNC_FLAGS "$EXTRACTED/" "$INSTALL_DIR/"

IMPRINT_BIN="$INSTALL_DIR/bin/imprint"
[ -f "$IMPRINT_BIN" ] || fail "Binary missing after extract: $IMPRINT_BIN"
chmod +x "$IMPRINT_BIN"
success "Binary ready at $IMPRINT_BIN"

# --- Symlink into PATH (do this before bootstrap so `imprint` is reachable) ---
info "Linking imprint into $BIN_DIR..."
mkdir -p "$BIN_DIR"
ln -sf "$IMPRINT_BIN" "$BIN_DIR/imprint"
success "Symlinked $BIN_DIR/imprint → $IMPRINT_BIN"

# --- Verify bundled uv is present ---
UV_BIN="$INSTALL_DIR/bin/uv"
if [ ! -x "$UV_BIN" ]; then
    fail "Bundled uv not found at $UV_BIN — release archive is broken. Re-download or re-run the installer."
fi
success "Bundled uv: $("$UV_BIN" --version 2>/dev/null || echo unknown)"

# --- Determine install profile (prompt unless --non-interactive / piped) ---
# NVIDIA auto-detect pre-selects the "gpu" default when nvidia-smi reports
# at least one device.
GPU_AVAILABLE=0
if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi -L 2>/dev/null | grep -q "GPU "; then
        GPU_AVAILABLE=1
    fi
fi

# tty-detect: interactive only when stdin IS a tty. `curl | bash` never is.
INTERACTIVE=0
if [ -t 0 ] && [ "$NON_INTERACTIVE" != "1" ]; then
    INTERACTIVE=1
fi

if [ -z "$PROFILE" ]; then
    if [ "$INTERACTIVE" = "1" ]; then
        default="cpu"
        [ "$GPU_AVAILABLE" = "1" ] && default="gpu"
        gpu_hint=""
        [ "$GPU_AVAILABLE" = "1" ] && gpu_hint=" (NVIDIA GPU detected)"
        printf "\033[0;36m[?]\033[0m Install profile [cpu/gpu] (default: %s)%s: " "$default" "$gpu_hint"
        read -r reply
        case "$(echo "${reply:-$default}" | tr '[:upper:]' '[:lower:]')" in
            gpu) PROFILE="gpu" ;;
            cpu|"") PROFILE="cpu" ;;
            *) PROFILE="$default"; info "Unknown choice — using $default" ;;
        esac
    else
        PROFILE="cpu"
    fi
fi

if [ -z "$WITH_LLM" ]; then
    if [ "$INTERACTIVE" = "1" ]; then
        printf "\033[0;36m[?]\033[0m Install local LLM tagger (llama-cpp-python, ~200 MB)? [y/N]: "
        read -r reply
        case "$(echo "${reply:-n}" | tr '[:upper:]' '[:lower:]')" in
            y|yes) WITH_LLM=1 ;;
            *) WITH_LLM=0 ;;
        esac
    else
        WITH_LLM=0
    fi
fi

if [ "$WITH_LLM" = "1" ]; then
    info "Profile: $PROFILE + local LLM tagger"
else
    info "Profile: $PROFILE (no local LLM)"
fi

# --- Run imprint bootstrap (uv-powered venv + deps) ---
info "Running imprint bootstrap (uv downloads its own Python — no host install required)..."
BOOTSTRAP_ARGS=("--profile" "$PROFILE")
if [ "$WITH_LLM" = "1" ]; then
    BOOTSTRAP_ARGS+=("--with-llm")
else
    BOOTSTRAP_ARGS+=("--no-llm")
fi
[ "$NON_INTERACTIVE" = "1" ] && BOOTSTRAP_ARGS+=("--non-interactive")
"$IMPRINT_BIN" bootstrap "${BOOTSTRAP_ARGS[@]}" || fail "imprint bootstrap failed"

# --- Register with Claude Code and other AI tools ---
info "Running imprint setup (MCP registration)..."
"$IMPRINT_BIN" setup

if [ "$EXISTING_INSTALL" = "1" ]; then
    success "Update complete. Preserved: data/ (workspaces, qdrant, sqlite, config, gpu_state.json, profile.json), .venv/"
    info "Future updates can use 'imprint update' directly — no curl required."
else
    success "Installation complete! Restart your terminal to use the 'imprint' command."
fi
