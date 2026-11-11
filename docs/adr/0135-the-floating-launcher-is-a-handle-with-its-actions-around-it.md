---
title: ADR 0135 — The floating launcher is a handle with its actions around it
description: While the dashboard is hidden, the Imprint button is an orb that drags everything docked to it; a click fans out Dashboard, Echo and Record in a separate window toward free space; Echo folds into the orb; a recording starts from the orb in the hidden dashboard after the same consent.
---

- Status: Amended
- Date: 2026-11-05
- Amends:
  - [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) — a click on the launcher opens its actions instead of the dashboard; Echo docks on the orb's open side and slides along it instead of moving the orb; Echo can fold into the orb, which hides its window
  - [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) — a recording can start and stop from the orb; the desktop app relays it to the dashboard window, which moves to the Recordings page to capture
- Amended by:
  - [ADR 0137](0137-echo-wakes-on-hey-echo-from-a-hidden-desktop-listener.md) — the orb shows an "ear" dot while the "Hey Echo" listener exists, and a pulse when it hears the phrase
  - [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md) — folded, Echo's page releases the microphone and lets go of the conversation, which goes on in the API and is picked up when Echo shows again, instead of stopping it
  - [ADR 0174](0174-the-floating-orb-is-off-unless-the-user-turns-it-on.md) — the orb appears while the dashboard is hidden only when the user turned it on, or while a recording runs
- Related: [ADR 0048](0048-anime-echo-and-desktop-recovery.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md), [ADR 0089](0089-one-progress-record-for-the-running-job.md), [dashboard](../dashboard.md), [live voice](../live-voice.md)

## Context

When the dashboard is hidden, the desktop app shows a 48 px launcher button. A click opened the dashboard and a right-click showed a menu with Echo. Echo docked beside it and either window dragged both (ADR 0070).

The user asked for the recorder and Echo to be reachable from that button, "revolving around" it, each opening from it and minimizing back into it, the dashboard too, with the Imprint icon "just a drag handle".

The constraints found in the code and in Electron 45:

- Each floating surface is its own OS window. Transparent pixels of an Electron window still take clicks unless the main process turns on click-through, and forwarding pointer moves while click-through is on works on macOS and Windows only. A large window around the button would block what is underneath.
- Growing a window up or to the left moves its origin. The page lays out a frame after the native resize, so content anchored to the old origin jumps for a frame.
- The recorder runs in the dashboard window's page (ADR 0131). The desktop app grants the microphone to the `/voice`, `/companion` and `/recordings` pages, and the computer's audio only to the dashboard window's Recordings page.
- Starting a recording in a hidden window from another window has no user gesture there. In Electron 45 on macOS, a `getDisplayMedia` call made through `executeJavaScript(…, false)` in a hidden window reached `setDisplayMediaRequestHandler` with and without a gesture (scratch experiment, 2026-11-05), so the relay needs no gesture workaround.
- A design pass (an interactive prototype and a written spec) compared a hover-opened ring, a press-and-hold ring, a full circle and a fan toward free space.

## Decision

**The orb is a handle.** While the dashboard is hidden, the launcher is a 48 px orb in a fixed 72 × 72 window; the extra room holds its status rim. A press that moves more than 4 px (8 px for touch and pen) drags it, and everything docked to it moves along. A click toggles its ring of actions. A double-click opens the dashboard. Right-click, Shift+F10, the Menu key and a 500 ms touch hold open the native menu: Open Dashboard, Show or Collapse Echo, New recording or Stop recording, and Quit. Alt+arrow keys move the orb by 16 px, or 64 px with Shift.

**The ring opens toward free space, in its own window.** Dashboard, Echo and Record are 40 px spheres 68 px from the orb's centre, 45° apart. They fan toward the wider half of the work area, or toward Echo's side when Echo is docked. The fan is level when 96 px are free above and below the orb, and otherwise tilts toward the roomier side, so every sphere stays on screen in any corner. The ring and its panels share one transparent window, created on first use and hidden between uses. It is sized for the ring, or for a panel, and shown only after its page reports that it has drawn the layout it was sent. The orb's own window never moves or resizes, so the orb never moves under the pointer. A sphere's name shows on hover, keyboard focus or a touch hold. Like a menu, the ring closes on Esc, on an action, on a click elsewhere (the window losing focus), on a drag, and after 6 s without the pointer when the pointer opened it. A press on the orb closes the ring, and that press's click does not reopen it. The keyboard follows the menu pattern: arrows, Home, End and Tab move, wrapping.

**Echo folds into the orb.** The Echo sphere, a collapse button in Echo's header while it is docked, and the menu hide Echo's window instead of destroying it. Hidden, Echo's page stops the conversation and the microphone, as it already did on any hide (ADR 0069). Showing Echo again docks the same window beside the orb. Echo docks on the orb's open side, centred on it and slid along it to stay on screen. The orb never moves to make room. When neither side has room, Echo stacks above or below the orb. A short arc on the orb's rim faces the docked Echo.

**Recording from the orb.** The Record sphere opens a panel with a title (empty means the server's "Meeting <date> <time>"), the project and the computer-audio switch, both remembered on this device, and the consent box. The consent box is never remembered, takes focus first, and Start stays disabled until it is ticked. The main process passes a start only from the orb's windows, and only with `consent: true` and a project. It sends the start to the dashboard window and stops throttling that window's timers while it records. The dashboard moves to the Recordings page if it is elsewhere, so the microphone and computer-audio rules stay as they were, and then starts its recorder. The dashboard reports its recorder's phase, title, start time and levels; the main process checks them and passes them to the orb and the ring. While recording, the orb shows the elapsed time and a red rim that glows with the louder channel. The Record sphere becomes Stop, and a capsule beside it shows the time and both levels and opens a details panel with Stop and transcribe. A start that fails keeps its panel open with the reason, and offers "Record my microphone only", the OS microphone settings and Try again. Quitting during a recording asks first, then stops it and waits up to 15 s for the last audio before quitting.

**The orb shows the running job.** Its rim shows the running job's progress from the same record the tray reads (ADR 0089), or spins when the total is unknown. A recording, a stopped recording's error (amber, until the ring is opened) and progress share the rim in that order.

**Appearance.** The orb stays navy on any wallpaper. The ring and its panels follow the system's light or dark appearance, and every motion has a reduced-motion form.

## Rejected alternatives

- **Hover opens the ring.** Touch screens have no hover, and the ring would open whenever the pointer passed by.
- **Press and hold opens the ring.** It competes with the start of a drag, and on Windows press-and-hold already means right-click.
- **One large launcher window with click-through.** It blocks clicks on Linux, where forwarding is unavailable, and toggling click-through per pointer move is fragile.
- **Growing the launcher window for the ring.** Opening up or to the left moves the window's origin, so the orb would jump for a frame.
- **A full circle of actions.** At edges and corners half of it falls off screen; a 90° fan toward free space always fits.
- **Recording in the orb's window.** The computer's audio is granted only to the dashboard's Recordings page (ADR 0131), and a second recorder would split the recording's state between windows.
- **A one-click Record sphere.** It would start a recording without the per-call consent ADR 0131 requires.
- **Keeping Echo listening while folded.** That would leave a live microphone with no visible surface, against ADR 0069.
- **Keeping the native menu as the main way in.** It cannot show a recording's time or levels and is hard to find and use on touch. It stays as the secondary menu with Quit.

## Consequences

- A single click no longer opens the dashboard; a double-click or the Dashboard sphere does.
- Echo stays in memory while folded, so it reopens without loading its page and model again.
- Starting a recording from the orb moves the hidden dashboard to the Recordings page, which is where the user finds it on reopening.
- The ring's window takes focus while open, so opening it activates Imprint, as clicking the old launcher did.
- Every open of the ring costs one IPC round trip before it shows.

## Invalidation conditions

- If Electron gains reliable click-through with pointer forwarding on Linux, one window for the orb and its ring can be reconsidered.
- If a platform puts the ring's window above the orb so the orb cannot be pressed, the ring's transparent spot over the orb, which already closes the ring, must also start drags.
- If users miss single-click-to-open-the-dashboard, swap the click and double-click actions.
- If recording moves out of the dashboard window (for example into a dedicated hidden window), the relay through the dashboard and its move to the Recordings page go with it.

## Validation

- `node --test` in `ui-electron` (macOS): the fan direction, tilt and on-screen bounds at nine positions in a work area; Echo docking without moving the orb, keeping its side and switching sides at an edge; the ring opening in its own window only after its page draws, closing on blur, a press on the orb not reopening it, and its actions coming only from its own page; a start refused without consent, a project or from another window, relayed once, unthrottling the dashboard, folding its panel when recording begins and reaching both windows; Echo folding into the orb and returning in the same window; quitting during a recording asking, stopping and then quitting.
- `imprint/ui/tests/launcher.spec.ts` and `voice.spec.ts`: the spheres at their laid-out positions, names on hover, keyboard order with wrap, Stop and the capsule while recording, the consent-gated panel with remembered project and its error actions, the details panel, the orb's recording state, and the orb's click, double-click, drag, arrow and Alt+arrow handling.
- `ui-electron/smoke-orb.mjs` passed in real Electron 45 on macOS (Apple M3) with the dashboard bundle, a fixture API and Chromium's fake microphone. It covered the ring beside a fixed orb, Echo docked and folded, a recording started from the panel with the hidden dashboard moving to Recordings and uploading audio, Stop from the ring, and a double-click opening the dashboard. The native voice workflow runs it on Windows, macOS and Linux.
- Not verified: a physical microphone, the computer's audio started from the orb, z-order and focus on native Windows and Linux desktops, touch input, and a screen reader.
