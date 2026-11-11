---
title: ADR 0179 — The macOS desktop app is com.alexandruleca.imprint.desktop
description: The Electron desktop app's bundle identifier moves from tech.brightspaces.imprint to com.alexandruleca.imprint.desktop, the project's own namespace, distinct from the installer's enclosing Imprint.app, com.alexandruleca.imprint.
---

- Status: Accepted
- Date: 2026-11-11
- Related: [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) (the ad-hoc signed desktop bundle), [ADR 0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) (privacy texts in the enclosing app), [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) (recording the computer's audio)

## Context

electron-builder's `appId` was `tech.brightspaces.imprint`, set with early packaging work on 2026-04-30 with no record of why. It is the Electron app's `CFBundleIdentifier` on macOS, its helpers' prefix, and the identity macOS privacy consent (TCC) keys grants to. Everything else in the macOS packaging uses the project's own namespace: the installer's enclosing `Imprint.app` (`com.alexandruleca.imprint`), its package and distribution (`com.alexandruleca`), and the CLI launcher (`com.alexandruleca.imprint.launcher`). The user asked for the desktop app to follow.

The `.pkg` installs the Electron app inside the enclosing `Imprint.app`, so the two bundles cannot share an identifier.

## Decision

The Electron app's `appId` is `com.alexandruleca.imprint.desktop`. electron-builder names its helpers after it (`com.alexandruleca.imprint.desktop.helper` and so on). The enclosing app stays `com.alexandruleca.imprint`. The release validator and its tests use the new identifiers, and a test keeps the two apart.

## Rejected alternatives

- **`com.alexandruleca.imprint` for the desktop app too.** The `.pkg` nests the desktop app inside an app with that identifier, and two nested bundles with one identifier leave macOS unsure which app a launch or a privacy grant belongs to.
- **Keeping `tech.brightspaces.imprint`.** It names no part of this project, and its only advantage, keeping existing grants, matters little: macOS ties grants to an ad-hoc signed app's exact build (ADR 0055), so updates already lose them in practice.

## Consequences

- macOS treats the desktop app as a new app once: it asks again for the microphone, and for system audio when a recording takes the computer's audio. Grants recorded for the old identifier no longer apply, and stale ones from earlier updates go with them.
- For `.pkg` installs, grants that macOS attributes to the enclosing app (`com.alexandruleca.imprint`) are unaffected.
- The desktop app's data folder follows the product name, `Imprint`, so it is unchanged. Windows releases ship electron-builder's portable build inside the Inno Setup installer, whose own `AppId` is a fixed GUID, so Windows installs and updates are unaffected.

## Invalidation conditions

- If the macOS app is ever signed with a Developer ID, keep this identifier: the signing requirement would then keep grants across updates.
- If the `.pkg` stops nesting the desktop app in its own `Imprint.app`, the desktop app could take `com.alexandruleca.imprint` itself.

## Validation

- `tests/test_release_validation.py` validates fixture bundles under the new identifiers. `tests/test_macos_microphone_bundle.py` checks the two identifiers stay distinct.
- Not run: a macOS build and install with the new identifier.
