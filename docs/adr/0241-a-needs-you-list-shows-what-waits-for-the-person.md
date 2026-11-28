---
title: ADR 0241 — A Needs-you list shows what waits for the person
description: Home and the Board show one list of what waits for the person, each row with its reason, which leaves only when the reason ends or the person dismisses it; the tray's and the orb's count include it, and closing a toast no longer marks a comment read.
---

- Status: Proposed
- Date: 2026-11-28
- Related: [ADR 0212](0212-agents-talk-through-comments-on-tasks.md) (toasts for comments and Reviews), [ADR 0182](0182-the-dashboard-has-a-simple-and-an-advanced-form.md) (Home's review inbox in the simple dashboard), [ADR 0232](0232-agents-get-their-own-desktop-window-and-a-fourth-orb-action.md) (the count of agents waiting for you), [ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md) (Tasks waiting for the person's move), [ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md) (roles)

## Context

The owner asked (2026-11-28): "I should also see on the dashboard tasks that need my attention, not just in the notification popup."

- Comments agents address to the user, and Tasks reaching Review, show as a toast and, in the desktop app, a desktop notification ([`board-alerts.tsx`](../../imprint/ui/src/components/board-alerts.tsx), [ADR 0212](0212-agents-talk-through-comments-on-tasks.md)). A comment counts as read once its toast is opened or closed, so after that it shows nowhere.
- The Board's "What needs you" chips filter the columns to Review, assigned to you, stale, stalling and blocked ([`board.tsx`](../../imprint/ui/src/components/agents/board/board.tsx)). They count no comments, and they live on the Agents page.
- Home shows no Tasks: Attention in the advanced dashboard and the review inbox in the simple one cover memories and Organize ([ADR 0182](0182-the-dashboard-has-a-simple-and-an-advanced-form.md)).
- Several things now wait for the person without anything showing them:
  - a Dispatcher that switched itself off;
  - Tasks Dispatched work fed, which wait for the person's move ([ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md));
  - a Stale Task left because its commit is on another device;
  - Review Tasks that no device will review.
- Another session (imprint-memory-layer-42) is adding a "needs your answer" state for agents on the Live page: a pending choice, a question at the end of a message, or a tool call waiting for approval. It agreed to expose it for this list rather than build a second one.

## Decision

**One list, with reasons.** A Needs-you list, served at `/api/tasks/needs-you`, gives each row its reason:
- unread comments addressed to the user;
- Tasks in Review that no paired device will review: none has the Review role ([ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md)), or the review-round limit was reached;
- open Tasks assigned to the user, including those that two failed reviews or two deploy conflicts handed over;
- Tasks waiting for the person to move them ([ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md));
- Stale Tasks left because their commit is on another device;
- a Dispatcher on this device that switched itself off after failures;
- agents waiting for the person's answer, from the Agents page's live state.

**Where.** On Home, in both the simple and the advanced dashboard, and at the top of the Board. The orb's and the tray's count of agents waiting for you ([ADR 0232](0232-agents-get-their-own-desktop-window-and-a-fourth-orb-action.md)) includes the list's rows, counting an agent once.

**When a row leaves.** When its reason ends: the person replied, moved the Task, or reassigned it. Or when the person dismisses it. A dismissal is kept as one of the user's read marks, so it reaches paired devices ([`merge.py`](../../imprint/tasks/merge.py)), and a dismissed row returns when its reason changes. Closing a toast no longer marks a comment read; opening its Task, replying, or dismissing the row does.

## Rejected alternatives

- **More toasts, or toasts that stay.** A toast is gone once seen, which is the problem the owner described. A toast that stays would cover the page it appears on.
- **A second list on the Agents page for agents awaiting an answer.** Two lists for one question, "what waits for me", with two counts that disagree. The Live state feeds this list instead, as agreed with the session building it.
- **Extending the Board's chips only.** They filter the columns of one workspace on the Agents page, and the person asked to see this on the dashboard.

## Consequences

- What waits for the person stays visible until it is dealt with, on the page the dashboard opens with.
- Toasts still announce new items, but are no longer their only record.
- The orb's count can grow beyond agents, since it now includes Tasks.
- The list reads the Board, the device's Dispatcher state and the Agents page's live state, so it depends on the roles and live states other records define.

## Invalidation conditions

- If the list regularly holds rows the person cannot act on, the reason that produces them is removed from it, and this record is amended.
- If the desktop app's notifications gain a persistent notification center, that may replace the orb's part of this record.

## Validation

Proposed; not yet implemented. To be validated by T-nm1x4 (API and Home) and T-qqk35 (Board, tray and orb).
