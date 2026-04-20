# Native installers

Native `.exe` and `.pkg` installers for end users who don't want to
`curl | bash`. Source for both lives here; CI builds them on platform-specific
runners and attaches the artifacts to each GitHub Release.

## Layout

```
installers/
├── windows/
│   ├── imprint.iss            # Inno Setup 6 script (single source of truth)
│   ├── imprint-setup.ps1      # Hidden bootstrap run from Inno [Run] at install
│   ├── imprint-launcher.ps1   # Shortcut target: starts services + opens UI
│   └── assets/imprint.ico     # CI-generated from site/public/logo.svg (gitignored)
└── macos/
    ├── build-pkg.sh           # Driver: pkgbuild → productbuild → imprint-darwin-<arch>.pkg
    ├── generate-icns.sh       # rsvg-convert → iconutil → AppIcon.icns
    ├── distribution.xml       # productbuild distribution template
    ├── scripts/
    │   └── postinstall        # Runs once at pkg install time (root, then sudo -u user)
    └── Imprint.app.template/  # /Applications/Imprint.app bundle skeleton
        └── Contents/
            ├── Info.plist
            └── MacOS/imprint-launcher
```

## What the installers do

Both install the exact same tree that the release archive contains
(`imprint-<os>-<arch>/` from `make package`), plus:

- **Desktop / Start Menu shortcut** pointing to a launcher script.
- **PATH entry** (`imprint` available in any shell).

### Install-time setup

The heavy first-run work runs **while the installer is still on screen**, so
the launcher itself has no wizard. Specifically:

- **Windows** — Inno Setup `[Run]` invokes `imprint-setup.ps1` hidden, which
  creates `.venv`, `pip install -r requirements.txt`, runs `imprint setup`,
  and drops `.first-run.done`.
- **macOS** — the `.pkg` postinstall script runs the same steps as the
  console user (`sudo -u`) in the background, then exits so the Installer UI
  closes promptly.

### Launcher responsibilities

The shortcut target (`imprint-launcher.ps1` / `Imprint.app/Contents/MacOS/imprint-launcher`)
does exactly two things:

1. Call `imprint ui open`, which boots the Qdrant daemon + UI server and
   opens the default browser.
2. Exit. Services keep running in the background.

If the sentinel is missing (user copied the `.app` bundle manually, postinstall
failed silently, etc.), the launcher falls back to running the bootstrap
itself — silently on macOS with a native notification, silently on Windows
with a WPF error dialog if anything breaks.

User data (`data/`, `.venv/`) is preserved on uninstall.

## Local build

### Windows

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) (`iscc` on PATH) and
a pre-built release archive for Windows.

```powershell
# From repo root, after `make all && make package`:
iscc installers\windows\imprint.iss `
    /DImprintVersion=0.6.4 `
    /DImprintSource=..\..\dist\imprint-windows-amd64 `
    /O..\..\dist
# Produces dist\imprint-windows-amd64-setup.exe
```

### macOS

Requires macOS (pkgbuild + productbuild are Apple-only) and a pre-built
release archive for the target arch.

```sh
# From repo root, after `make all && make package`:
./installers/macos/build-pkg.sh \
    --version 0.6.4 \
    --arch arm64 \
    --source dist/imprint-darwin-arm64 \
    --out dist/imprint-darwin-arm64.pkg
```

## Code signing and notarization (not enabled by default)

Unsigned installers trigger SmartScreen (Windows) and Gatekeeper (macOS)
warnings. v1 ships unsigned — users right-click → Open on macOS and click
"More info → Run anyway" on Windows. To enable signing, set these repo
secrets:

| Platform | Secret | Used by |
|---|---|---|
| Windows | `WINDOWS_CODESIGN_PFX` (base64), `WINDOWS_CODESIGN_PASSWORD` | `signtool.exe` post-Inno |
| macOS | `MACOS_CERT_P12`, `MACOS_CERT_PASSWORD`, `MACOS_NOTARY_USER`, `MACOS_NOTARY_PASSWORD`, `MACOS_TEAM_ID` | `codesign` + `xcrun notarytool` |

The release workflow branches automatically when these are present.

## Asset naming

Installer filenames match the regex used by `site/scripts/build-releases.mjs`
so the download page picks them up automatically:

- `imprint-windows-amd64-setup.exe`
- `imprint-darwin-amd64.pkg`
- `imprint-darwin-arm64.pkg`

## CI coverage

Both `.github/workflows/release.yml` (stable) and `.github/workflows/dev-release.yml`
(prereleases on pushes to `dev`) run the `package-windows` and `package-macos`
jobs after the archive-building release job completes. So every stable tag
*and* every dev build gets an `.exe` + two `.pkg` files attached, and the
download page picks them up on its next rebuild (currently ≤1h cache).

## App icons

Generated at release time from `site/public/logo.svg`:

- **Windows** — ImageMagick (`choco install imagemagick`) renders the SVG
  into a multi-resolution `.ico` at `installers/windows/assets/imprint.ico`
  (gitignored). Inno's `#ifexist` falls back to the exe icon if the file is
  missing, so `iscc` still works locally without ImageMagick.
- **macOS** — `installers/macos/generate-icns.sh` uses `rsvg-convert` +
  `iconutil` to produce `Contents/Resources/AppIcon.icns`. Install locally
  with `brew install librsvg`; the script is a no-op without it.
