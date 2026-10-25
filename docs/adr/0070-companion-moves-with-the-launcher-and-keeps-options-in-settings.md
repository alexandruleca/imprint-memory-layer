---
title: ADR 0070 — The floating companion moves with the launcher and keeps its options in settings
description: While the dashboard is hidden the companion docks beside the floating launcher and either window drags both; only the head and header drag it; everything except talking sits in a settings panel.
---

- Status: Accepted
- Date: 2026-10-25
- Scope: implementation; native Windows and macOS window behavior is unvalidated (see Validation)
- Amends: [ADR 0048](0048-anime-echo-and-desktop-recovery.md) — its rule that any non-interactive area of the companion drags it, and its launcher that moves independently of the companion
- Related: [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md), [ADR 0043](0043-live-voice-and-echo-companion.md), [ADR 0054](0054-echo-looks-declare-their-motion.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md)

## Context

Two sessions changed the floating companion from the same starting point (ADR 0048) and this record joins them.

- The owner asked for one movable floating group when the dashboard is hidden, and for fewer controls on screen. They also reported that clicking the microphone checkbox or the reasoning selector dragged the avatar. The companion attached its pointer-drag handler to the whole surface and skipped a list of interactive tags; that list missed labels and the checkbox primitive.
- Separately, v0.22 reports led to fixes on the integration branch: the question field could not be clicked, the window kept following the cursor after the OS swallowed a release, and the pin button showed no state. Those fixes made the head, the header and a grip strip the only drag handles, ended a drag on a move without the primary button, and made the pin button show the state the main process broadcasts.
- The companion and the launcher are separate sandboxed windows. The launcher route has no microphone permission ([ADR 0048](0048-anime-echo-and-desktop-recovery.md)).
- In a real Electron run of the grouped drag, the two windows' position reports arrived independently and late: reading both bounds when the pointer was released sometimes saw one window moved and the other not, which changed the spacing between them.

## Decision

**The companion travels with the launcher while the dashboard is hidden.** The two windows stay separate. When the dashboard is closed, hidden or minimized and the companion is open, the main process docks the head beside the launcher, or above it when the work area is too narrow. A drag in either window moves both, from a snapshot of both bounds taken when the drag starts. When the drag ends, the positions the drag intended, not the windows' reported bounds, are clamped together into one work area and both are saved. Display changes and opening or recalling the companion dock it again. While grouped, the companion stays on top. The user's **Keep on top** choice is separate state in the main process; the companion's pin button shows that choice, and reopening the dashboard restores it.

**Only the head and the header drag the companion.** The control area, the window actions and anything portaled out of the surface (the settings panel) never start a drag, and no drag starts while the settings panel is open: a press on the head then closes the panel. A move without the primary button ends a drag. No native drag region covers a control; the panel and everything in it are marked no-drag.

**Everything except talking is in a settings panel.** The companion shows the microphone mode, the current action and the status line. A **Companion settings** button opens a shadcn (Base UI) popover with Echo's look, the reasoning model, the hands-free threshold, the question field and transcript, Interrupt, Stop, Keep on top, Close, and how to move and recall Echo. It closes with Escape or a click outside. The Voice page has the same button for its reasoning model and threshold and keeps its look picker, question field and transcript on the page.

## Rejected alternatives

- **One renderer for the launcher and the companion.** It would give the launcher the companion's microphone permission and needs a window migration; the pair already moves as one through the main process.
- **Whole-surface dragging with a longer exclusion list.** Portaled and custom controls are easy to miss, which is how the checkbox and the reasoning selector came to drag the avatar. A short list of handles is easier to verify.
- **Keeping the grip strip as a third handle.** It carried the move and recall hint, which is now in the settings panel; the head and header are large enough handles.
- **Keeping every option on screen.** It conflicts with the requested compact companion.
- **Reading both windows' bounds when a group drag ends.** Native position reports lag independently, as the native run showed, so the spacing would drift.

## Consequences

- When the dashboard is hidden, the head and the launcher move and stay together; once the dashboard is open, the head moves on its own again.
- Stop, Close, typing and the transcript take one more click in the companion, and the panel covers part of the head while it is open.
- Recalling Echo while the dashboard is hidden docks it beside the launcher rather than on the cursor's display.
- The grouped companion is always on top, whatever its pin choice, until the dashboard opens.
- Positions are saved for both windows after every group drag.

## Invalidation conditions

- If Electron child windows follow their parent's moves on Windows and Linux as they do on macOS, the main process's paired moves and intended-position clamp can give way to a parent and child window.
- If the launcher ever needs microphone or voice state, a single renderer for both windows can be reconsidered.
- If people cannot find Stop or Close behind the settings button in a usability check, those controls return to the companion's surface.
- If the companion gets a native frame or title bar, the head and header drag handles can be revisited, as ADR 0048 already notes.

## Validation

- `node --test` in `ui-electron` (Linux x64, WSL2): the hidden dashboard docks the companion, a drag from either window moves both and keeps their spacing, a pin choice made while grouped is reported as the user's choice while the head stays on top, reopening the dashboard restores it and undocks the head, the group stays inside a work area and stacks on a narrow one, and a lagging native position report does not change the spacing.
- Playwright against the production dashboard build: the companion shows no settings until asked, every control in the panel computes `app-region: no-drag`, clicks in the panel never start a drag, Escape and a press on the head close the panel, the head then drags again, a missed release ends a drag, and pin shows the broadcast state.
- `ui-electron/smoke-companion-controls.mjs` passed in real Electron 45 under WSLg (`--no-sandbox`, software WebGL, a disposable profile and fixture API): it opened the panel, chose the reasoning model in the native select with the keyboard, picked a look, hid the dashboard and dragged the docked launcher and then the head, checking that both moved and kept their spacing after their positions settled. The native voice workflow runs it on Windows, macOS and Linux.
- Not executed: the grouped windows on native Windows and macOS desktops, compositors other than WSLg, and a usability check of the settings panel.
