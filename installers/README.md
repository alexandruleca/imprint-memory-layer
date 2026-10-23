# Native installers

Windows x64 uses Inno Setup; macOS Intel/ARM use pkgbuild/productbuild. Both consume a validated staged release tree from `make package` or `make package-local`. See [building](../docs/building.md), [platform validation](../docs/platform-validation.md) and [ADR 0002](../docs/adr/0002-build-and-validate-before-publication.md).

Windows installs per user under `%LOCALAPPDATA%\Programs\Imprint`. Its wizard runs the bundled uv/bootstrap path and client registration, displaying the log. The final `.setup-result` reports completion; `.first-run.done` alone does not. Silent installs run the setup script explicitly. The Repair shortcut reuses saved profile choices. Data and the virtual environment are retained on uninstall.

macOS installs `/Applications/Imprint.app` and a `/usr/local/bin/imprint` link. Root postinstall only fixes permissions/quarantine and the CLI link. First launch provisions Python/dependencies in the user's writable `~/.local/share/imprint` through the native Swift progress helper, with a Terminal fallback. It does not install Python as root. Source resources remain inside the app bundle.

These installers bundle the CLI, source, static dashboard, Electron shell and uv, but download managed Python, wheels, Qdrant and models as needed. They require a network on first use. Signing/notarization, fully offline payloads and interactive GUI validation are still outstanding; do not treat the local Linux checks as macOS/Windows execution evidence.

Build the shell and dashboard first, then the platform archive, then `make installer-macos ARCH=arm64` on macOS or `make installer-windows` with Inno Setup on Windows. The shared release workflow performs this ordering and includes every deliverable in `SHA256SUMS` before publication.
