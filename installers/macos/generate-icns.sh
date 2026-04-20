#!/bin/bash
# Render site/public/logo.svg into a macOS .icns file.
#
# Usage: generate-icns.sh <logo.svg> <output.icns>
#
# Requires macOS built-ins (iconutil, sips) plus rsvg-convert for SVG→PNG.
# Install rsvg-convert with `brew install librsvg`. If it's missing the
# script prints a warning and exits 0 (the caller continues without an icon).

set -euo pipefail

SVG="${1:-}"
OUT="${2:-}"

[ -n "$SVG" ] && [ -n "$OUT" ] || {
    echo "usage: $0 <logo.svg> <output.icns>" >&2
    exit 64
}
[ -f "$SVG" ] || { echo "[!] logo not found: $SVG" >&2; exit 0; }

if ! command -v rsvg-convert >/dev/null; then
    echo "[!] rsvg-convert not installed — skipping icon. Install with: brew install librsvg"
    exit 0
fi
command -v iconutil >/dev/null || { echo "[!] iconutil missing (macOS only)"; exit 0; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ICONSET="$STAGE/AppIcon.iconset"
mkdir -p "$ICONSET"

render() {
    local size="$1" out="$2"
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$ICONSET/$out"
}

# Apple's required iconset sizes.
render 16    icon_16x16.png
render 32    icon_16x16@2x.png
render 32    icon_32x32.png
render 64    icon_32x32@2x.png
render 128   icon_128x128.png
render 256   icon_128x128@2x.png
render 256   icon_256x256.png
render 512   icon_256x256@2x.png
render 512   icon_512x512.png
render 1024  icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$OUT"
echo "[+] Wrote $OUT"
