#!/bin/bash
# Double-click to uninstall Imprint. Runs `imprint uninstall` to unwire the
# MCP hooks, stop every Imprint process and remove the runtime and data
# (ADR 0028), then removes the launcher's first-run marker, the emptied
# writable folder, /Applications/Imprint.app, /usr/local/bin/imprint, and
# this command file itself.
set -eu

APP="/Applications/Imprint.app"
SYMLINK="/usr/local/bin/imprint"
IMPRINT="$APP/Contents/Resources/imprint/bin/imprint"
WRITABLE_HOME="$HOME/.local/share/imprint"

echo ""
echo "================ Uninstall Imprint ================"
echo ""
echo "This will:"
echo "  * Unregister Imprint's MCP server from Claude Code + other hosts"
echo "  * Stop Imprint, its local service, MCP servers and Qdrant"
echo "  * Remove the Python runtime"
echo "  * Delete /Applications/Imprint.app"
echo "  * Delete /usr/local/bin/imprint"
echo ""
read -r -p "Continue? [y/N] " ans
case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
esac
read -r -p "Also delete your memories, workspaces and settings in ~/.local/share/imprint and ~/Library/Application Support/imprint-ui? [Y/n] " ans
case "$ans" in
    n|N|no|NO) DATA_FLAG="--keep-data" ;;
    *) DATA_FLAG="--delete-data" ;;
esac

echo ""
if [ -x "$IMPRINT" ]; then
    echo "[*] Running 'imprint uninstall'..."
    if ! "$IMPRINT" uninstall -y "$DATA_FLAG"; then
        echo "[!] Cleanup failed. App preserved; resolve the error and retry."
        exit 1
    fi
else
    echo "[!] Imprint binary missing; repair the app before uninstalling."
    exit 1
fi

# The launcher's marker goes with the app. The folder goes once empty: kept
# data, or a script install sharing the path, keeps it.
rm -f "$WRITABLE_HOME/.first-run.done"
rmdir "$WRITABLE_HOME" 2>/dev/null || true

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
