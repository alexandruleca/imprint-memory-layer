#!/usr/bin/env bash
# Stage one target; never reuse an Electron executable from a different arch.
set -euo pipefail
PLATFORM="${1:?usage: package-platform.sh os/arch}"
OS="${PLATFORM%/*}"
ARCH="${PLATFORM#*/}"
case "$PLATFORM" in
    linux/amd64|linux/arm64|darwin/amd64|darwin/arm64|windows/amd64) ;;
    *) echo "Unsupported release platform: $PLATFORM" >&2; exit 1 ;;
esac
EXT=""
[ "$OS" != windows ] || EXT=".exe"
NAME="imprint-$OS-$ARCH"
STAGE="dist/$NAME"
SOURCE_BIN="bin/$NAME$EXT"
[ -f "$SOURCE_BIN" ] || { echo "Missing $SOURCE_BIN; run make all" >&2; exit 1; }
command -v rsync >/dev/null
command -v python3 >/dev/null
rm -rf "$STAGE"
mkdir -p "$STAGE"
rsync -a --exclude-from=.releaseignore ./ "$STAGE/"
mkdir -p "$STAGE/bin"
cp "$SOURCE_BIN" "$STAGE/bin/imprint$EXT"
chmod +x "$STAGE/bin/imprint$EXT"
bash scripts/fetch-uv.sh "$OS" "$ARCH" "$STAGE"
case "$OS" in
    linux) UI="build/electron/linux-$ARCH/imprint-ui"; UI_NAME="imprint-ui" ;;
    darwin) UI="build/electron/imprint-ui.app"; UI_NAME="imprint-ui.app" ;;
    windows) UI="build/electron/imprint-ui.exe"; UI_NAME="imprint-ui.exe" ;;
esac
if [ -e "$UI" ]; then
    cp -R "$UI" "$STAGE/bin/$UI_NAME"
    [ "$OS" != linux ] || chmod +x "$STAGE/bin/$UI_NAME"
elif [ "${REQUIRE_ELECTRON:-0}" = 1 ]; then
    echo "Missing Electron app: $UI" >&2; exit 1
else
    echo "[!] $UI absent; packaging browser dashboard only" >&2
fi
python3 scripts/validate-release.py "$STAGE" "$PLATFORM"
if [ "$OS" = windows ]; then
    rm -f "dist/$NAME.zip"
    python3 - "$NAME" <<'PY'
import shutil, sys
shutil.make_archive('dist/' + sys.argv[1], 'zip', 'dist', sys.argv[1])
PY
else
    tar -czf "dist/$NAME.tar.gz" -C dist "$NAME"
fi
echo "[+] Packaged $NAME (staged tree retained at $STAGE)"
