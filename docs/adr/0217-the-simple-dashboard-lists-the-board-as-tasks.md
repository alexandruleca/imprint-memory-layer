---
title: ADR 0217 — The simple dashboard lists the Board as Tasks
description: The simple dashboard lists a twelfth page, Tasks, among its everyday pages after Reminders; it is the Agents page showing the Board alone, while the sessions and their tool calls stay in the advanced dashboard.
---

- Status: Amended
- Date: 2026-11-27
- Amends: [ADR 0182](0182-the-dashboard-has-a-simple-and-an-advanced-form.md) — the simple dashboard's page list (eleven pages)
- Amended by: [ADR 0233](0233-live-reads-running-agents-from-local-records-and-transcripts-without-a-model.md) — its "the Board alone": the Tasks page shows Live beside the Board
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0183](0183-each-page-has-a-short-tutorial-that-can-be-skipped.md) (tutorials), [dashboard](../dashboard.md#simple-and-advanced)

## Context

[ADR 0182](0182-the-dashboard-has-a-simple-and-an-advanced-form.md) lists eleven pages in the simple dashboard and leaves Agents, among others, to the advanced one. The Board became a tab of the Agents page ([ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md)). The usability review of the Board on 2026-11-27 found that a simple dashboard's user reached it only through a notification or its address, under the note that the page belongs to the advanced dashboard; the owner then asked to "add it to simple mode".

The Agents page also has a Sessions tab: every MCP session and its tool calls, with arguments and errors ([ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md)). Those are the implementation's words that ADR 0182 keeps out of the simple dashboard.

## Decision

The simple dashboard lists twelve pages. **Tasks** (`/agents`) joins the everyday pages after Reminders. In the simple dashboard the page is titled Tasks and shows the Board alone, without the Sessions tab and without the note for advanced pages; the Board itself already leaves out the history, lease times, branch and files there. The advanced dashboard keeps the page as Agents with both tabs. The page's tutorial has a first step for each form.

## Rejected alternatives

- **Leaving the page unlisted.** The finding above: the people who most need to see what their agents are doing and what waits for them would not find it.
- **Listing Agents with both tabs.** The Sessions tab's tool calls and errors are what ADR 0182 keeps out of the simple dashboard.

## Consequences

- A simple dashboard's sidebar has seven everyday pages.
- `/agents` shows different titles and content per form, as other shared pages already trim theirs.
- Opening `/agents/#sessions` in the simple dashboard shows the Board; Sessions is one switch to Advanced away.

## Invalidation conditions

- If simple dashboards' users rarely open Tasks (no visits recorded over a month once the dashboard counts page visits), move it back to the advanced dashboard.
- If the simple dashboard needs to show agent sessions in plain words, give Tasks a sessions view of its own in a new record.

## Validation

- Playwright, Linux: `ui-mode.spec.ts` (the simple sidebar's twelve pages in order), `board.spec.ts` (Tasks current in the sidebar, the Board without tabs or the advanced note, the drawer without history or lease details), `page-tours.spec.ts` (each form's tutorial steps point at controls of the page).
- Not run: Windows and macOS browsers; the desktop app.
