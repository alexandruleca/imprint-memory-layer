---
title: ADR 0069 — Echo opens the microphone only on the user's intent, after an OS consent check
description: Enable microphone, or Start conversation once the user has chosen the microphone, asks the operating system for consent through scoped IPC before opening the device; the device remembers the choice, never the grant.
---

- Status: Accepted
- Date: 2026-10-25
- Scope: implementation; interactive macOS consent, Windows privacy settings and physical devices are unvalidated (see Validation)
- Amends:
  - [ADR 0043](0043-live-voice-and-echo-companion.md) — its rule that microphone access is always a separate action from starting a conversation: once the user has chosen the microphone, a conversation turns it on when it is ready
  - [ADR 0044](0044-native-desktop-voice-platforms.md) — its macOS-only consent check inside Electron's permission request: an explicit consent check now precedes capture on macOS and Windows
- Related: [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md), [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md)

## Context

**Enable microphone** crashed the installed macOS app. Two sessions investigated it from the same code.

- One found that the pkg's outer launcher, `Imprint.app`, declared no `NSMicrophoneUsageDescription`, while the nested Electron app did, and proposed declaring it in both bundles with PlistBuddy checks in the Makefile and `build-pkg.sh`. It had no crash log or Mac to confirm the cause.
- The other inspected the published v0.22.1 artifact. The Electron app was unsigned, so nothing sealed its Info.plist to its identity, and the shell is started through LaunchServices (`open -a`, or by the OS directly), so it is its own responsible process. [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) signs the app ad hoc and rejected adding the text to `Imprint.app`.

Consent lived only inside Electron's permission request callback. The renderer learned nothing about the operating system's answer before capture: a refusal, a policy restriction and a failed status query all looked alike or never reached it. On Windows the callback granted audio without reading the desktop privacy setting. The native desktop smoke ran with `--use-fake-ui-for-media-stream`, which skips the permission handlers entirely, so CI never exercised them. A separate Windows report showed that failures were often not about permission at all; the integration branch already names the step that failed (device, worklet or graph) in a `microphoneError` snapshot field.

The owner also asked that the microphone choice survive restarting a conversation, without clicking **Enable microphone** every time.

## Decision

**The operating system is asked first.** Turning the microphone on starts with a `permission` step: the renderer asks the main process (`requestMicrophoneAccess`), and only then opens the device, loads the capture worklet and connects the graph.

- macOS reads the consent status and calls `askForMediaAccess` only while it is `not-determined`. Capture proceeds only on `granted`.
- Windows reads `getMediaAccessStatus`. Capture proceeds unless it reports `denied` or `restricted`.
- Linux has no consent API in Electron and answers `unmanaged`; capture itself decides.
- A status query that throws answers `error`, never a grant.

**The IPC is as narrow as the capture it precedes.** The consent call and a second call that opens the fixed macOS or Windows microphone settings page answer only the owned Voice and companion main frames; the consent call checks the sender again after the user answers. Electron's permission request and check handlers stay the authority for capture itself: audio only, owned voice main frames only; camera, mixed audio and video, subframes and other pages stay refused. The request handler's macOS consent uses the same function.

**Each failure says what happened.** The renderer keeps the integration branch's named steps and `microphoneError` field. A refusal names the recovery for its platform (on macOS: allow Imprint, then quit and reopen it); a policy restriction points to the administrator; a failed check says to restart; a page without `getUserMedia` says to use the local address or HTTPS. A refusal on desktop macOS or Windows also offers **Open microphone settings** (`microphoneSettings`). Stop, navigation or turning the choice off while the system dialog is open drops its late answer, so the device never opens afterwards.

**The device remembers the choice, never the grant.** A successful **Enable microphone** stores `microphoneEnabled`, with the talk mode and the reasoning model, under `imprint.voice.preferences.v1` in the browser or desktop profile's local storage. Malformed or unavailable storage means microphone off, push-to-talk and the chat model. While the choice is on, a conversation that becomes ready turns the microphone on through the same consent step, whether a button or a typed question started it. Opening, recalling or setting up the companion never captures. Clearing **Use microphone for conversations** in Companion settings releases capture and drops a pending consent, and keeps the conversation for typing. Stop, navigation and Quit release capture and keep the choice. Nothing is synced or written to settings or memories.

**The bundle that asks declares why.** The desktop app's own Info.plist carries `NSMicrophoneUsageDescription` (from electron-builder's `extendInfo`), sealed by its ad-hoc signature. Release validation rejects a darwin tree whose app declares no non-empty description. The `Imprint.app` launcher declares none.

## Rejected alternatives

- **Consent only inside Electron's permission request callback.** The renderer cannot tell a refusal from a restriction or a failed query, and Windows privacy was never read. The callback stays as the enforcement point.
- **Asking at startup or during setup.** Unrelated to anything the user did with the microphone.
- **`askForMediaAccess` on every platform.** Electron offers it only on macOS; Windows desktop privacy and Linux permissions work differently.
- **Resetting TCC or changing privacy settings automatically.** It would override the user's choice and cannot lift a policy restriction.
- **Relaxing the media permission filter.** A Linux Electron probe showed it admits normal audio capture; the audio, main-frame and origin boundary need not weaken.
- **Storing the OS grant.** Only the OS can decide; storing intent and asking each time keeps it the authority.
- **Listening whenever the companion appears.** Arming the microphone when a conversation the user started is ready removes the repeated click without capture the user did not start.
- **Usage text in the outer `Imprint.app` with PlistBuddy checks in the Makefile and `build-pkg.sh`.** ADR 0055 showed the launcher's Info.plist is not the one macOS consults, and those checks would be a second, macOS-only path for what the release validator checks on Linux for the bundle that does ask.

## Consequences

- Two scoped IPC calls exist; neither accepts a URL or reaches other settings.
- A previously denied macOS user still changes the setting and fully restarts Imprint.
- macOS asks again after every update (ADR 0055); with the choice remembered, that prompt appears when a conversation starts.
- The choice belongs to one browser or desktop profile: the browser dashboard and the desktop app remember separately, and clearing site data forgets it.
- The production handlers now run in the native smokes. If a hosted runner's privacy settings refuse desktop microphone access, the smokes report it instead of passing.
- Installed apps need a new release for the consent step and the validator's check to apply.

## Invalidation conditions

- If the desktop app's microphone requests are ever attributed to the `Imprint.app` launcher (for example because the launcher starts the shell as a child process instead of through LaunchServices), the launcher must declare the usage text too.
- If Electron gains a consent status or prompt for Linux desktop portals, Linux gets the same explicit check instead of letting capture decide.
- If people are surprised by the microphone turning on when a conversation starts, in a usability check or support reports, the remembered choice stops arming capture automatically.
- If Electron's permission request reports the operating system's answer to the page, the separate consent IPC can fold back into it.

## Validation

- `node --test` in `ui-electron` (Linux x64, WSL2) passed: consent answers per platform and status, a single prompt only for undetermined macOS consent, `error` on a failed query, IPC refusing other pages and subframes, the sender rechecked after consent, Windows denial without a prompt, Linux without consent or a settings link, and the permission request refusing a page that navigated away during consent.
- `imprint/ui/scripts/voice-audio.test.mjs` passed: preferences keep only supported values and recover from broken storage; consent answers map to proceed or refuse as above.
- Playwright against the production dashboard build passed: a refusal on macOS, Windows and Linux shows its own recovery, survives a mode change, offers the settings link only on macOS and Windows and is followed by a capturing retry; a restriction and a failed check are not called refusals; a missing microphone in the companion offers no settings link; Stop during a pending consent and clearing the choice during one never capture; an enabled microphone and the talk mode carry across navigation, opening the companion does not capture, and clearing the choice keeps typing.
- `tests/test_release_validation.py` passed: a darwin tree whose app has no or an empty usage description is rejected; the desktop build configuration declares one.
- Real Electron 45 under WSLg (`--no-sandbox`, disposable profiles, synthetic devices, no fake permission prompt): `smoke-microphone.mjs` passed, with audio captured on Voice and companion main frames and refused for camera, mixed media, other pages and subframes, and the consent IPC refusing a non-voice page; `smoke-companion-controls.mjs` passed, clicking the real checkbox and starting a second conversation that turned the synthetic microphone on without another click. The native voice workflow runs both on Windows, macOS and Linux.
- Not executed: the interactive macOS prompt and recovery on Intel and Apple Silicon, Windows privacy toggles (including hosted-runner defaults), physical microphones, Linux portals and packaged sandboxing, and a signed release built with the validator's check.
