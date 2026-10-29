---
title: ADR 0055 — Ad-hoc signed macOS desktop bundle
description: The macOS desktop app is signed ad hoc as a bundle, so macOS privacy consent can read its microphone usage text; release packaging rejects an unsigned bundle.
---

- Status: Amended
- Date: 2026-10-25
- Amends: [ADR 0044](0044-native-desktop-voice-platforms.md) — its statement that the app includes microphone/JIT entitlements
- Amended by:
  - [ADR 0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) — the enclosing installer app also requires microphone usage text
  - [ADR 0111](0111-intel-macs-are-not-supported.md) — the desktop app is built for arm64 only, not as a universal binary
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (Developer ID signing and notarization still need maintainer credentials)
- Restructured: 2026-10-25

## Context

On macOS v0.22.x, **Enable microphone** in Echo (voice page or companion) crashes the desktop app. The native desktop smoke launches Electron with `--use-fake-ui-for-media-stream`, which skips the macOS privacy (TCC) path, so CI never reached it.

Inspection of the published `imprint-darwin-arm64.tar.gz` for v0.22.1 (checksum matches `SHA256SUMS`) shows that `bin/imprint-ui.app` was never signed:

- No bundle in it has `_CodeSignature/CodeResources`: not the app, not its four helper apps.
- The main executable is a universal binary. Its x86_64 slice has no `LC_CODE_SIGNATURE` at all. Its arm64 slice has only the linker's signature: flags `adhoc | linker-signed` (`0x20002`), identifier `Electron` rather than `tech.brightspaces.imprint`, a CodeDirectory with no special slots and no requirements or entitlements blobs. The Info.plist and the sealed resources are not bound to the code, and there is no hardened-runtime flag. The helpers and `Electron Framework` are in the same state.
- `Contents/Info.plist` does contain `NSMicrophoneUsageDescription` (from `extendInfo`), but nothing seals it to the app's code identity.

The release workflow builds the app with electron-builder on a macOS runner without a signing identity. electron-builder 26 then logs "skipped macOS application code signing" as a warning and leaves the Electron binaries as downloaded and renamed. The `entitlements`, `entitlementsInherit` and default hardened runtime in `package.json` are only applied while signing, so none of them took effect. ADR 0044's "the app includes microphone/JIT entitlements" was never true of a release.

When the user enables the microphone, the main process calls `systemPreferences.askForMediaAccess('microphone')`. macOS privacy consent identifies the requesting app through its code signature, and it terminates a process whose usage description it cannot establish (`Namespace TCC`, "attempted to access privacy-sensitive data without a usage description"). An app whose Info.plist is not bound to its signature, and which is signed under a different identifier, is that case. Other Electron projects report the same failure: after patching Info.plist without re-signing, microphone access fails until the bundle is signed ad hoc. The unsigned bundle is established from the artifact. That it is what TCC rejects is the best-supported explanation, but it is inferred: the user's crash report (Termination Reason) has not been seen yet.

The launch path does not attribute the request to another app. `imprint ui open` starts the shell with `open -a <bin>/imprint-ui.app` (LaunchServices), so the shell is its own responsible process, not `Imprint.app` (whose Info.plist has no usage text) and not Terminal.

## Decision

The macOS desktop app is signed ad hoc, as a bundle, by electron-builder (`mac.identity: "-"`). Signing runs after the universal merge and seals every helper, framework and the app, with `Info.plist` and `CodeResources` bound under each bundle's own identifier.

Hardened runtime is off (`mac.hardenedRuntime: false`), and the project's entitlements file is removed. Hardened runtime matters for notarization, which needs a Developer ID. Without notarization it only adds failure modes: with an ad-hoc signature, library validation would reject Electron's own framework unless it is disabled, and the microphone would additionally need the `audio-input` entitlement. The JIT and audio-input entitlements only have an effect under hardened runtime.

Release packaging rejects a darwin tree whose `bin/imprint-ui.app`, or any helper app inside it, is not signed as a bundle. `scripts/validate-release.py` parses each bundle executable's code signature on the Linux packaging runner. Every architecture slice must carry a CodeDirectory whose identifier is the bundle's `CFBundleIdentifier` and whose special slots hold the hashes of the bundle's actual `Info.plist` and `_CodeSignature/CodeResources`.

## Rejected alternatives

- **Add `NSMicrophoneUsageDescription` to `Imprint.app`'s Info.plist.** Not the cause: the shell is started through LaunchServices and is its own responsible process. It would leave the shell's own request unsealed.
- **Keep hardened runtime and add `disable-library-validation`.** This is what electron-builder recommends for ad-hoc signing with hardened runtime. It gives no security benefit without notarization and adds a launch-time failure mode that no CI job executes the packaged app to catch.
- **Developer ID signing and notarization.** The correct end state, giving a stable identity (TCC grants that survive updates) and Gatekeeper approval. It needs maintainer credentials and secrets and remains open. Moving to it means a real identity in place of `-`, hardened runtime on, and the JIT and audio-input entitlements back.
- **Re-sign with `codesign --deep` in the Makefile.** electron-builder already signs inside-out with per-helper options; a second signer would be a parallel path for the same step.

## Consequences

- The release app has a valid ad-hoc bundle signature, so TCC can read the usage text and prompts for the microphone instead of terminating the app.
- An ad-hoc identity is its code hash. Each release is a new identity to TCC, so macOS asks for microphone access again after an update.
- The package is still not Developer ID signed or notarized. `postinstall` still clears quarantine, and the existing signing/notarization gaps stay open.
- A release that loses the signature (for example a missing identity setting or an edit to the bundle after signing) now fails packaging on Linux.

## Invalidation conditions

- When maintainer Developer ID credentials are available, sign and notarize with hardened runtime and the JIT and audio-input entitlements; the record names that the correct end state, and it replaces ad-hoc signing.
- If the user's crash report shows a termination reason other than the inferred TCC rejection, the diagnosis behind this decision must be revisited.

## Validation

- The validator rejects the published v0.22.1 bundle ("Unsigned macOS bundle (no sealed resources)") and its four helpers.
- `tests/test_release_validation.py` builds synthetic universal bundles: an ad-hoc bundle signature passes, while a linker-only signature, a signature under the binary's name, an Info.plist edited after signing and an unsigned helper are rejected.
- Not yet verified on a Mac: a release built with this configuration, `codesign --verify --deep --strict` of the installed app, and the interactive TCC prompt on **Enable microphone** on macOS 13 or later, for both an Apple Silicon and an Intel machine.
