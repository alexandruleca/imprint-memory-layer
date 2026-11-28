---
title: ADR 0232 — Agents get their own desktop window and a fourth action around the orb
description: The desktop app opens the Agents page (Live, Board and History) in a window of its own, without the sidebar, from the tray, a fourth action in the orb's ring and Task notifications, while the main dashboard window keeps recordings, updates and navigation.
---

- Status: Proposed
- Date: 2026-11-28
- Related: [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) (its ring of three actions gains a fourth, Agents; the amend links go in both records when this one is accepted), [ADR 0233](0233-live-reads-running-agents-from-local-records-and-transcripts-without-a-model.md) (Live, the window's first tab), [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (session history), [ADR 0174](0174-the-floating-orb-is-off-unless-the-user-turns-it-on.md) (when the orb shows)

## Context

The owner asked on 2026-11-28 whether the Agents page should be "its own dashboard window" with "its own button around the floating button and in the task icon menu", to "separate concerns". In a three-round design session the same day, the owner accepted every recommendation:

- one Agents window with Live, Board and History tabs;
- opened from the tray, a fourth orb action, and Task notifications;
- the main dashboard keeps the same tabs at the same addresses;
- the number of agents waiting for you badges the orb and the tray.

What the desktop app assumes today ([`ui-electron/main.js`](../../ui-electron/main.js), [`companion.js`](../../ui-electron/companion.js)):

- **One dashboard window.** It is referenced in about 60 places. The orb shows while it is hidden; recordings run through the hidden dashboard window ([ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md)); update restarts and navigation go through it.
- **A ring of exactly three satellites.** Each tilt (level, up, down) has three angles, and Record's capsule has a fixed slot.

## Decision

**An Agents window.** The desktop app opens the dashboard's Agents page in a second window, without the sidebar, showing its Live, Board and History tabs. It is reused while open and remembers its size and place. It opens from **Open Agents** in the tray menu, from an **Agents** action in the orb's ring, and from Task notifications (a comment for you, a Task reaching Review), which open the Task there.

**The main window keeps its jobs.** Recordings, updates, navigation and the orb's rules stay with the main dashboard window. The orb shows while the main window is hidden, whether or not the Agents window is open, and closing the Agents window never quits the app.

**The ring has four actions.** Dashboard, Echo, Record and Agents. Angles, labels and radius are laid out for four, so that nothing overlaps or leaves the work area at any edge or corner, and Record's capsule keeps its place.

**A browser keeps everything.** The same page and tabs stay at their addresses in the main dashboard.

## Rejected alternatives

- **The tray entry alone, with the orb's fourth action later.** Recommended first as the cheapest step. The owner asked for both, and the window is reached most often from the orb.
- **A separate app or bundle for agents.** It would duplicate the dashboard's data layer and settings for no gain. A second window on the same pages costs one more renderer.
- **Moving recordings or updates to whichever window is open.** Those flows depend on the main window ([ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md)), and moving them gains nothing for agents.

## Consequences

- Agents can stay open on another screen while the main dashboard is used for memory, chat or settings.
- A second renderer adds memory (roughly 100–200 MB), only while the window is open.
- The ring's layout and its smoke tests change for four actions.

## Invalidation conditions

- If the ring cannot fit four actions without overlap at screen corners on supported display scales, move Agents out of the ring and keep the tray entry.
- If Electron gains reliable click-through with pointer forwarding on every platform, one window for the orb and its ring can be reconsidered, as [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) notes.

## Validation

Proposed: nothing here is implemented yet. Tasks T-fjy6w (the tabs), T-qj45f (the window), T-pa1mc (the ring) and T-h4rjd (the badge) on the Board carry it out, and T-k2k4x accepts this record.
