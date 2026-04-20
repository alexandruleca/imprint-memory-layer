#!/usr/bin/env bash
# Download Astral's `uv` binary for the target (OS, ARCH) and drop it into
# dist/imprint-<os>-<arch>/bin/uv. Called from the `fetch-uv` Makefile
# target before `make package` tars up each release archive.
#
# Usage: scripts/fetch-uv.sh <os> <arch> <dest_dir>
#   os:   linux | darwin | windows
#   arch: amd64 | arm64
#   dest_dir: where the staged install tree already lives (we write into
#             $dest_dir/bin/uv[.exe])
#
# Pin UV_VERSION deliberately per release — never track `latest`. Bump
# alongside the Imprint tag so release reproducibility is preserved.
set -euo pipefail

UV_VERSION="${UV_VERSION:-0.5.11}"
OS="${1:?os required: linux|darwin|windows}"
ARCH="${2:?arch required: amd64|arm64}"
DEST_DIR="${3:?dest_dir required}"

case "$OS-$ARCH" in
    linux-amd64)   TARGET="x86_64-unknown-linux-gnu";   ARCHIVE_EXT="tar.gz"; BIN_NAME="uv" ;;
    linux-arm64)   TARGET="aarch64-unknown-linux-gnu";  ARCHIVE_EXT="tar.gz"; BIN_NAME="uv" ;;
    darwin-amd64)  TARGET="x86_64-apple-darwin";        ARCHIVE_EXT="tar.gz"; BIN_NAME="uv" ;;
    darwin-arm64)  TARGET="aarch64-apple-darwin";       ARCHIVE_EXT="tar.gz"; BIN_NAME="uv" ;;
    windows-amd64) TARGET="x86_64-pc-windows-msvc";     ARCHIVE_EXT="zip";    BIN_NAME="uv.exe" ;;
    windows-arm64) TARGET="aarch64-pc-windows-msvc";    ARCHIVE_EXT="zip";    BIN_NAME="uv.exe" ;;
    *) echo "[x] unsupported target: $OS-$ARCH" >&2; exit 1 ;;
esac

ASSET="uv-${TARGET}.${ARCHIVE_EXT}"
URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${ASSET}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[*] fetching uv ${UV_VERSION} for ${OS}-${ARCH} → ${URL}"
if command -v curl >/dev/null; then
    curl -fsSL "$URL" -o "$TMP/$ASSET"
elif command -v wget >/dev/null; then
    wget -q "$URL" -O "$TMP/$ASSET"
else
    echo "[x] need curl or wget" >&2; exit 1
fi

mkdir -p "$DEST_DIR/bin"
if [ "$ARCHIVE_EXT" = "zip" ]; then
    # Prefer `unzip`; fall back to Python's zipfile module (python3 is
    # already a prereq of `make package` for zip creation, so it's always
    # present on the runner).
    if command -v unzip >/dev/null; then
        unzip -qq -o "$TMP/$ASSET" -d "$TMP/extract"
    elif command -v python3 >/dev/null; then
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/$ASSET" "$TMP/extract"
    else
        echo "[x] neither unzip nor python3 found (needed to extract windows uv zip)" >&2
        exit 1
    fi
    # Windows zips nest the binary under uv-<target>/
    if [ -f "$TMP/extract/uv-${TARGET}/$BIN_NAME" ]; then
        cp "$TMP/extract/uv-${TARGET}/$BIN_NAME" "$DEST_DIR/bin/$BIN_NAME"
    else
        cp "$TMP/extract/$BIN_NAME" "$DEST_DIR/bin/$BIN_NAME"
    fi
else
    tar -xzf "$TMP/$ASSET" -C "$TMP"
    # tarballs nest the binary under uv-<target>/uv
    if [ -f "$TMP/uv-${TARGET}/$BIN_NAME" ]; then
        cp "$TMP/uv-${TARGET}/$BIN_NAME" "$DEST_DIR/bin/$BIN_NAME"
    else
        cp "$TMP/$BIN_NAME" "$DEST_DIR/bin/$BIN_NAME"
    fi
    chmod +x "$DEST_DIR/bin/$BIN_NAME"
fi

echo "[+] $DEST_DIR/bin/$BIN_NAME ($(wc -c < "$DEST_DIR/bin/$BIN_NAME") bytes)"

# Fetch uv's own LICENSE texts alongside the binary — required for Apache
# 2.0 compliance (we redistribute uv in binary form, so the recipient must
# receive the license). uv is dual-licensed Apache-2.0 OR MIT; we ship both
# so the downstream consumer can pick.
LIC_DIR="$DEST_DIR/licenses/uv"
mkdir -p "$LIC_DIR"
for lic in LICENSE-APACHE LICENSE-MIT; do
    url="https://raw.githubusercontent.com/astral-sh/uv/${UV_VERSION}/${lic}"
    if command -v curl >/dev/null; then
        curl -fsSL "$url" -o "$LIC_DIR/$lic" || echo "[!] failed to fetch $lic (non-fatal)" >&2
    else
        wget -q "$url" -O "$LIC_DIR/$lic" || echo "[!] failed to fetch $lic (non-fatal)" >&2
    fi
done
cat > "$LIC_DIR/README.md" <<EOF
# uv ${UV_VERSION}

Astral's \`uv\` binary ships at \`bin/${BIN_NAME}\`. Dual-licensed Apache-2.0
**or** MIT at the recipient's choice — full texts in this directory.

Upstream: https://github.com/astral-sh/uv
EOF
echo "[+] $LIC_DIR/ (uv license texts)"
