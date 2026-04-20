#!/bin/bash
# Build imprint-darwin-<arch>.pkg from an extracted release tree.
#
# Usage:
#   installers/macos/build-pkg.sh \
#       --version 0.6.4 \
#       --arch arm64 \
#       --source dist/imprint-darwin-arm64 \
#       --out    dist/imprint-darwin-arm64.pkg
#
# The "release tree" is the directory produced by `make package` after
# untarring (contains bin/imprint, imprint/, requirements.txt, ...).
#
# Requires: pkgbuild, productbuild (macOS CLI tools).

set -euo pipefail

VERSION=""
ARCH=""
SOURCE=""
OUT=""

die()  { echo "[x] $*" >&2; exit 1; }
info() { echo "[*] $*"; }
ok()   { echo "[+] $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --arch)    ARCH="$2"; shift 2 ;;
        --source)  SOURCE="$2"; shift 2 ;;
        --out)     OUT="$2"; shift 2 ;;
        *) die "Unknown arg: $1" ;;
    esac
done

[ -n "$VERSION" ]        || die "--version required"
[ -n "$ARCH" ]           || die "--arch required (amd64|arm64)"
[ -n "$SOURCE" ]         || die "--source required"
[ -n "$OUT" ]            || die "--out required"
[ -d "$SOURCE" ]         || die "source dir not found: $SOURCE"
[ -x "$SOURCE/bin/imprint" ] || die "binary not found/executable: $SOURCE/bin/imprint"

command -v pkgbuild >/dev/null     || die "pkgbuild not found (macOS only)"
command -v productbuild >/dev/null || die "productbuild not found (macOS only)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_TEMPLATE="$SCRIPT_DIR/Imprint.app.template"
DIST_XML="$SCRIPT_DIR/distribution.xml"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"
RESOURCES_DIR="$SCRIPT_DIR/resources"

[ -d "$APP_TEMPLATE" ] || die "missing Imprint.app.template at $APP_TEMPLATE"
[ -f "$DIST_XML" ]     || die "missing distribution.xml at $DIST_XML"
[ -d "$SCRIPTS_DIR" ]  || die "missing scripts/ at $SCRIPTS_DIR"

# productbuild expects x86_64 not amd64.
case "$ARCH" in
    amd64) PB_ARCH="x86_64" ;;
    arm64) PB_ARCH="arm64" ;;
    *)     die "unknown arch: $ARCH (expected amd64|arm64)" ;;
esac

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/imprint-pkg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/root"
APP_DIR="$ROOT/Applications/Imprint.app"
BUILD_RES="$STAGE/resources"

info "Staging .app at $APP_DIR"
mkdir -p "$APP_DIR"
cp -R "$APP_TEMPLATE/" "$APP_DIR/"

# Ship "Uninstall Imprint.command" alongside the .app so double-click works.
if [ -f "$SCRIPT_DIR/Uninstall Imprint.command" ]; then
    cp "$SCRIPT_DIR/Uninstall Imprint.command" "$ROOT/Applications/"
    chmod +x "$ROOT/Applications/Uninstall Imprint.command"
fi

# Fill in version placeholders in Info.plist.
/usr/bin/sed -i '' "s/__IMPRINT_VERSION__/${VERSION}/g" "$APP_DIR/Contents/Info.plist"

# Copy the release tree into Resources/imprint/ (this is what the launcher
# points at).
mkdir -p "$APP_DIR/Contents/Resources/imprint"
# Use rsync so we preserve executability of bin/imprint.
rsync -a --delete "$SOURCE/" "$APP_DIR/Contents/Resources/imprint/"
chmod +x "$APP_DIR/Contents/Resources/imprint/bin/imprint"
chmod +x "$APP_DIR/Contents/MacOS/imprint-launcher"

# Optional app icon (requires rsvg-convert). generate-icns.sh is a no-op
# when the tool is missing, so the build keeps going without an icon.
ICNS_PATH="$APP_DIR/Contents/Resources/AppIcon.icns"
LOGO_SVG="$(cd "$SCRIPT_DIR/../.." && pwd)/site/public/logo.svg"
if [ -f "$LOGO_SVG" ]; then
    "$SCRIPT_DIR/generate-icns.sh" "$LOGO_SVG" "$ICNS_PATH" || true
fi
if [ -f "$ICNS_PATH" ]; then
    # Info.plist CFBundleIconFile has no placeholder yet — patch it in.
    /usr/bin/sed -i '' \
        -e 's|<key>CFBundleExecutable</key>|<key>CFBundleIconFile</key>\
    <string>AppIcon</string>\
    <key>CFBundleExecutable</key>|' \
        "$APP_DIR/Contents/Info.plist"
fi

# --- Build welcome / conclusion / license resources ---
info "Generating installer resources..."
mkdir -p "$BUILD_RES"

# LICENSE: pkgbuild wants plain text.
if [ -f "$SOURCE/LICENSE" ]; then
    cp "$SOURCE/LICENSE" "$BUILD_RES/LICENSE.txt"
else
    echo "Licensed under the Apache License 2.0." > "$BUILD_RES/LICENSE.txt"
fi

cat > "$BUILD_RES/welcome.html" <<HTML
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 24px; color: #222; }
  h1 { font-size: 18px; margin-top: 0; }
  p { line-height: 1.5; }
  code { font-family: "SF Mono", Menlo, monospace; background: #f2f2f2; padding: 2px 4px; border-radius: 3px; }
  ul { line-height: 1.7; }
</style></head><body>
<h1>Imprint ${VERSION}</h1>
<p>Persistent semantic memory for your AI coding tools. 100% local, MCP-native.</p>
<p>This installer will:</p>
<ul>
  <li>Install <code>Imprint.app</code> into <code>/Applications</code>.</li>
  <li>Symlink the <code>imprint</code> CLI into <code>/usr/local/bin</code>.</li>
  <li>Run first-time setup (Python venv, MCP registration) in the background.</li>
</ul>
<p>Requires <strong>Python 3.9+</strong> and the <strong>Claude Code CLI</strong>.</p>
</body></html>
HTML

cat > "$BUILD_RES/conclusion.html" <<HTML
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 24px; color: #222; }
  h1 { font-size: 18px; margin-top: 0; }
  p { line-height: 1.5; }
  code { font-family: "SF Mono", Menlo, monospace; background: #f2f2f2; padding: 2px 4px; border-radius: 3px; }
</style></head><body>
<h1>Imprint is installed</h1>
<p>Launch <strong>Imprint</strong> from Launchpad or Spotlight — the web UI will open in your default browser.</p>
<p>From a terminal, the <code>imprint</code> command is available on <code>PATH</code>.</p>
<p>If setup did not finish automatically, open Terminal and run <code>imprint setup</code>.</p>
</body></html>
HTML

# --- Component pkg ---
COMPONENT_PKG="$STAGE/imprint-component.pkg"
IDENT="com.alexandruleca.imprint"

info "Building component package..."
pkgbuild \
    --root "$ROOT" \
    --identifier "$IDENT" \
    --version "$VERSION" \
    --install-location "/" \
    --scripts "$SCRIPTS_DIR" \
    "$COMPONENT_PKG"

# --- Distribution pkg ---
info "Building distribution package..."
DIST_STAGED="$STAGE/distribution.xml"
/usr/bin/sed \
    -e "s/__VERSION__/${VERSION}/g" \
    -e "s/__ARCH__/${PB_ARCH}/g" \
    "$DIST_XML" > "$DIST_STAGED"

mkdir -p "$(dirname "$OUT")"
productbuild \
    --distribution "$DIST_STAGED" \
    --package-path "$STAGE" \
    --resources "$BUILD_RES" \
    "$OUT"

ok "Wrote $OUT"
