#!/bin/bash
# Double-click to uninstall Imprint. Runs `imprint uninstall` to unwire the
# MCP hooks, then removes /Applications/Imprint.app, /usr/local/bin/imprint,
# and this command file itself.
set -eu

APP="/Applications/Imprint.app"
SYMLINK="/usr/local/bin/imprint"
IMPRINT="$APP/Contents/Resources/imprint/bin/imprint"

echo ""
echo "================ Uninstall Imprint ================"
echo ""
echo "This will:"
echo "  * Unregister Imprint's MCP server from Claude Code + other hosts"
echo "  * Remove the Python runtime (preserve indexed memories)"
echo "  * Delete /Applications/Imprint.app"
echo "  * Delete /usr/local/bin/imprint"
echo ""
read -r -p "Continue? [y/N] " ans
case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
esac

echo ""
if [ -x "$IMPRINT" ]; then
    echo "[*] Running 'imprint uninstall'..."
    if ! "$IMPRINT" uninstall -y --keep-data; then
        echo "[!] Client cleanup failed. App preserved; resolve the error and retry."
        exit 1
    fi
else
    echo "[!] Imprint binary missing; repair the app before uninstalling."
    exit 1
fi

echo ""
echo "[*] Removing /usr/local/bin/imprint (needs sudo)..."
if [ -L "$SYMLINK" ] && [ "$(readlink "$SYMLINK")" = "$IMPRINT" ]; then
    sudo rm -f "$SYMLINK"
fi

echo "[*] Removing $APP (needs sudo)..."
sudo rm -rf "$APP"

# Self-delete last so the user sees the success message.
SELF="$0"
echo ""
echo "[+] Imprint has been removed. You can close this window."
rm -f "$SELF" 2>/dev/null || true
