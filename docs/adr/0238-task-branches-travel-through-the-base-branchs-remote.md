---
title: ADR 0238 — Task branches travel through the base branch's remote
description: When a Dispatched work run ends in Review, Imprint, not the session, pushes the Task's branch to the remote its base branch tracks, and other devices fetch it before they review, take over or deploy.
---

- Status: Proposed
- Date: 2026-11-28
- Related: [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (its "Never push or merge" and "Takeovers from another device", which this would amend), [ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md) (roles), [ADR 0239](0239-the-dispatcher-deploys-tasks-a-dispatched-review-passed.md) (deploy)

## Context

With roles ([ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md)), one device can review what another device's Dispatcher worked on. The work has to reach it first.

- A device reviews or takes over a Task only when the Handoff's last commit exists in its copy of the repository. Otherwise it comments once and leaves the Task ([`guards.py`](../../imprint/dispatch/guards.py), `takeover`; [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)).
- Dispatched sessions never push, and the Dispatcher never fetches: [`git.py`](../../imprint/dispatch/git.py) has no fetch, and `has_commit` reads only the local object store.
- A Task branch therefore stays on the device that worked on it. A review-only device could only review Tasks whose commits the person pushed by hand.

## Decision

**Imprint pushes the Task's branch.** When a Dispatched work run ends with its Task in Review, Imprint pushes `task/<id>-<slug>` to the remote the run's base branch tracks, without force. The session itself still never pushes. A failed push leaves a comment on the Task, which stays in Review.

**No upstream, no push.** A base branch without an upstream pushes nothing. Other devices then leave the Task's reviews and takeovers as before, and the missing-work comment says the branch was not pushed.

**Others fetch before they check.** Before checking a Handoff's commit for a review, a takeover or a deploy, the Dispatcher fetches the Task's branch from that remote, at most once per Task per pass. A failed fetch is logged, and the check goes on with what is there.

**Cleanup.** Deploying deletes the remote Task branch ([ADR 0239](0239-the-dispatcher-deploys-tasks-a-dispatched-review-passed.md)). Otherwise branches stay, as local Task branches do today.

## Rejected alternatives

- **A git bundle sent through Imprint's sync relay.** It is private and needs no remote, but it means a new dataset, transfer limits and a bundle store on every device: a far larger build. The owner chose the remote (2026-11-28).
- **Reviewing only commits the person pushed.** That is today's behaviour, and it is the problem: work done by a device's Dispatcher never reaches a review-only device.

## Consequences

- Task branches appear on the remote, where anyone with access to the repository sees them until a deploy deletes them.
- Pushing uses the device's own git credentials. A device without push access leaves a comment and keeps the work local, as before.
- Fetching adds a network round trip before a cross-device review, a takeover or a deploy.

## Invalidation conditions

- If a project must not publish work in progress to its remote, Task branches need another path, such as the sync-relay bundle rejected above, and this record is reopened.
- If fetching before each check makes a pass slow enough to matter (more than a few seconds per Task), fetches are batched per project per pass.

## Validation

Proposed; not yet implemented. To be validated by T-713d6, including a test with two clones of one bare remote.
