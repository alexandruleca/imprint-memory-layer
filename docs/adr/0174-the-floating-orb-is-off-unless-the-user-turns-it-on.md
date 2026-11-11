---
title: ADR 0174 — The floating orb is off unless the user turns it on
description: The desktop app shows the floating orb while the dashboard is hidden only when the desktop.floating_orb setting is on, off by default, and during a recording; without it a minimized dashboard stays in the taskbar and a closed one goes to the tray.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) — the orb appears while the dashboard is hidden only when the user turned it on, or while a recording runs
- Related: [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) (Echo docks to the orb), [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) (recordings), [dashboard](../dashboard.md)

## Context

Since ADR 0135, hiding the dashboard by closing or minimizing it always brings up the floating orb, an always-on-top window over every other application. The user asked for a setting for it, off by default, so the desktop app is not intrusive.

The orb is also the only place that shows a recording while the dashboard is hidden: the tray says what the running job is doing (ADR 0089) and whether Imprint listens for "Hey Echo", but not that a recording is running. A recording can capture the computer's audio, so it must not run hidden without a sign.

## Decision

A setting, `desktop.floating_orb` (Settings → Desktop app; env `IMPRINT_DESKTOP_FLOATING_ORB`), off by default, decides whether the orb comes up while the dashboard is hidden. The desktop app reads it from the server (`GET /api/config/desktop.floating_orb`) each time the dashboard is hidden, so a change applies at the next hide without a restart; when the server does not answer within 800 ms, its last answer stands, at first off.

- **Off:** minimizing leaves the dashboard minimized in the taskbar; closing hides it to the tray (on macOS the Dock icon goes too), and on desktops without a tray minimizes it. No orb comes up.
- **On:** as ADR 0135 describes: closing or minimizing hides the dashboard to the tray and brings up the orb.
- **During a recording**, the orb comes up when the dashboard is hidden whatever the setting, and when the recording ends it goes again if the setting is off.

Closing hides the window at once; only the orb waits for the setting.

## Rejected alternatives

- **A choice kept in the desktop profile, like "Hey Echo"'s `wake.json` (ADR 0137).** That file holds state a setting cannot (a pause until a time, a consent flow). The orb is a plain preference; as a setting it is in Settings with the rest, settable with `imprint config set`, and needs no new bridge between the dashboard and the main process.
- **No orb during a recording when it is off.** The recording would run with nothing on screen saying so; the tray does not show recordings.
- **Hiding a minimized dashboard to the tray even with the orb off.** With neither orb nor taskbar button, the only way back would be the tray, which the user does not expect after minimizing.

## Consequences

- Hiding the dashboard no longer puts a window over other applications unless the user wants it, or a recording runs.
- With the orb off, recording from the orb and docking Echo to it are not available; recordings start from the Recordings page, Echo floats where it was and comes back from the tray's **Float Echo** or the shortcut. Folding Echo hides it the same way.
- Existing installations change behaviour on update: users who relied on the orb turn it on in Settings.
- Hiding makes one local HTTP request.

## Invalidation conditions

- If the tray (or the operating system's own indicator) shows a running recording and stops it, drop the exception that brings the orb up during a recording when the setting is off.
- If most installations turn the orb on, make it the default again.

## Validation

- `ui-electron/main.test.js` (Node 22, the main process with Electron replaced by test doubles): with the setting off, minimizing opens no orb and leaves the window as the system minimized it, closing hides it at once to the tray without an orb, and a recording brings the orb up and takes it away when it ends; the existing orb tests pass with it on. All 75 desktop-app tests pass.
- The Python suite covers the setting and `GET /api/config/{key}` on Windows 11.
- Not run: the packaged desktop app on Windows, macOS or Linux; behaviour with real windows (taskbar, tray, Dock) is unverified.
