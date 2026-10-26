---
title: ADR 0074 — Uninstall removes the shared desktop profile
description: An uninstall that deletes data also deletes the desktop shell's Electron profile, which every Imprint installation on the account shares.
---

- Status: Accepted
- Date: 2026-10-26
- Amends: [ADR 0028](0028-complete-uninstall.md) — what uninstall deletes with the data: the desktop profile as well
- Related: [ADR 0025](0025-desktop-owned-background-service.md), [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md)

## Context

The maintainer asked where macOS keeps Imprint's data, so that deleting the app removes all of it. [ADR 0028](0028-complete-uninstall.md) makes uninstall remove the runtime and data unless the user keeps the data. `imprint uninstall` deleted the data directory, the venv, the managed Python and the uv cache, and nothing else.

The desktop shell keeps a profile that is in none of those places. Electron puts it at `app.getPath('userData')`: its per-user application data directory (`~/Library/Application Support` on macOS, `%APPDATA%` on Windows, `$XDG_CONFIG_HOME` or `~/.config` on Linux) joined with the app's name. Electron takes that name from the shipped `package.json`, `productName` first, else `name`. The packaged app's `package.json` (read from `dist/electron/linux-unpacked/resources/app.asar`) has only `"name": "imprint-ui"`, because `productName` is set in electron-builder's `build` config, which Electron does not read. The profile is therefore `imprint-ui` under that directory.

The profile holds the companion's and launcher's window positions (`echo-window.json` and `launcher-window.json`, written by `ui-electron/main.js`) and Chromium's session data, including the dashboard's `localStorage` (chat, graph and voice preferences). Microphone permission is decided by request handlers in `main.js` and is not stored there.

No uninstaller removed it: not the macOS command, not the Windows uninstaller, and not `imprint uninstall` for archive and script installs. Every installation uses the same profile. `imprint ui open` starts the shell without `--user-data-dir` (`launchElectron` in `cmd/ui.go`), and a checkout's `npx electron ui-electron/` reads the same `package.json`.

## Decision

When uninstall deletes data, it also deletes the desktop profile. Data is deleted by default; it is kept with `--keep-data`, with the **No** answer, or with `/KEEPDATA`, and the profile is then kept too. This happens in `imprint uninstall`, which every native uninstaller calls, so every installation type gets it.

`platform.DesktopProfileDir()` locates the profile as `os.UserConfigDir()` joined with `imprint-ui`. Go's per-user configuration directory is the same directory as Electron's `appData` on macOS, Windows and Linux. A Go test derives the expected name from `ui-electron/package.json` the way Electron does (with electron-builder's `extraMetadata` merged in), so renaming the app or adding a top-level `productName` fails the test instead of leaving the profile behind.

The profile is removed before the data directory. On Windows a file another installation's running shell holds open cannot be deleted. That failure then stops the uninstall before memories are touched.

The confirmation prompt names the profile's path next to the data directory.

## Rejected alternatives

- **A profile per installation, inside its data directory.** `imprint ui open` would pass `--user-data-dir=<data>/desktop`, and the existing data removal and `--keep-data` would cover the profile with no new code. The shell takes its single-instance lock at startup (`app.requestSingleInstanceLock()` in `main.js`), and that lock is keyed on the profile. When the operating system starts the app itself (Dock, Reopen after a crash, a pinned icon), it passes no arguments. The shell then learns its runtime from `imprint ui runtime` only after startup (`desktopRuntime` in `ui-electron/runtime.js`). Such a launch would use the default profile and could run beside a shell that uses the per-installation one. Moving the profile would also reset the window positions every current user has.
- **Removing the profile in each native uninstaller** (the macOS command, and an Inno `[UninstallDelete]` entry for `{userappdata}\imprint-ui`). The same rule would live in two installer languages, each repeating the keep-data choice. Archive and script installs would still leave the profile.
- **Keeping the profile, the previous behavior.** An uninstall meant to remove Imprint completely left the dashboard's stored preferences and window state behind, and a reinstall inherited them.

## Consequences

- Uninstalling one installation and deleting its data resets the desktop profile of every other Imprint installation on the account: window positions and the dashboard's browser storage. Their memories and settings are untouched. The usual case is a source checkout beside a release install.
- If removing the profile fails, the runtime is already gone and the memories are still there, as with any data-removal failure. Running setup again repairs the installation.
- Keeping data keeps the profile, so a reinstall restores the window positions and dashboard preferences.
- Files macOS keeps for the app's bundle identifier outside the profile are not removed.

## Invalidation conditions

- If the shell chooses its profile location itself, through `app.setPath('userData')` by default or a per-installation profile, `DesktopProfileDir` must follow it. If that profile moves into the data directory, the data removal covers it and this record is superseded. The Go test catches a change of name, but not a `setPath` call.
- If Electron's `appData` stops matching Go's `os.UserConfigDir()` on a supported platform, the location rule is wrong there and must change.
- If the profile starts holding state that users expect to survive an uninstall that deletes their memories, the profile's removal is reopened.

## Validation

- **Go tests (Linux, WSL2):** `go test ./internal/platform ./cmd` passes. `TestDesktopProfileDirIsElectronsProfile` fails when the directory name is changed to `Imprint`. `go vet` passes for `GOOS=windows` and `GOOS=darwin`.
- **Sandboxed uninstall (Linux):** the release CLI built from this change ran `imprint uninstall -y` under bubblewrap. The sandbox had its own home, no Windows mounts, a non-WSL `/proc/version` and its own process namespace. With `--delete-data` it removed `~/.config/imprint-ui`, the data directory and the venv. With `--keep-data` it kept the profile and the data and removed the venv.
- **Packaged name:** read from the Linux package's `app.asar` (`name: imprint-ui`, no `productName`).
- **Release smoke:** the native Windows silent-uninstall smoke now creates `%APPDATA%\imprint-ui` and fails if uninstall leaves it. It has not run yet.
- **Not verified here:** a native macOS uninstall, and Electron's profile path on native macOS and Windows. Those rely on Electron's documented `appData` and name rule.
