---
title: ADR 0239 — The Dispatcher deploys Tasks a Dispatched review passed
description: On a device with the Deploy role, Imprint's own git code merges the branch of each Task a Dispatched review passed into the branch its work started from and pushes it, without force and never to the remote's default branch unless the person allows it.
---

- Status: Proposed
- Date: 2026-11-28
- Related: [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (its "Never push or merge" and its rejected "reviewer merging a passing branch", which this would amend), [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md) (a pass names the branch to merge, which this would amend), [ADR 0234](0234-the-dispatcher-counts-review-rounds-and-failures-across-paired-devices.md) (end-of-run tags), [ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md) (the Deploy role), [ADR 0238](0238-task-branches-travel-through-the-base-branchs-remote.md) (Task branches on the remote)

## Context

The owner asked (2026-11-28) for a device that will "review and deploy only". Asked what deploying means, the owner chose: merge the Task's branch into the branch the work started from and push it.

- Today a passing review moves the Task to Done, and Imprint's note says the branch "is ready to merge" ([`dispatched.py`](../../imprint/dispatch/dispatched.py), `finish`). The person merges ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)). ADR 0223 rejected the reviewer merging, because "an unattended merge into the branch the person works on would surprise them". This record answers that differently: the person now switches the merge on.
- The first dispatch records the base branch in the Task's first checkpoint ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)).
- Imprint's note at the end of every Dispatched run carries a tag `{run, role, outcome, started}` that syncs with the Board ([ADR 0234](0234-the-dispatcher-counts-review-rounds-and-failures-across-paired-devices.md)). So any device can tell that a Dispatched review passed: role review, outcome passed.
- Codex's sandbox keeps `.git` read-only and has no network by default ([ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md)). Claude Code's `--permission-mode auto` checks each action. An agent asked to push may not be allowed to.
- In this repository several sessions share one checkout on `dev`, with uncommitted work from each. A merge that moved the local `dev` would leave that checkout's index and files behind the branch.

## Decision

**Who deploys.** A device whose Deploy role is on ([ADR 0237](0237-a-devices-dispatcher-does-only-the-roles-the-person-switches-on.md)). The work is done by Imprint's own git code: no client session is started. A deploy counts toward neither `dispatch.concurrency` nor `dispatch.daily_limit`.

**What it deploys.** Done Tasks whose latest Dispatched review passed, by the synced end-of-run tags, and whose branch head is not yet in `<remote>/<base>`. Tasks the person moved to Done are left alone. Done keeps meaning "the review passed"; a "Deployed" note marks the merge.

**How.**
1. Fetch the base and the Task's branch ([ADR 0238](0238-task-branches-travel-through-the-base-branchs-remote.md)).
2. In a throwaway detached worktree on `<remote>/<base>`, fast-forward to the branch when the base has not moved. Otherwise make a merge commit, "Merge task/… (T-xxxxx)", with the repository's configured git identity and no trailers, when it merges without conflicts.
3. Push to `<base>` on the remote, never with force.

The person's checkout and local branches are never touched; the person pulls.

**Where it may push.** Only to the Task's recorded base. Never to the remote's default branch, unless the person lists that branch in `dispatch.deploy_branches`.

**After it.** A "Deployed `<commit>` to `<base>`" note, tagged with role deploy. The remote Task branch is deleted and the worktree removed. A branch already in the base, merged by the person or by another device, gets the same note and nothing is pushed. Pushes are never forced and an already-merged branch is recognised, so two deploying devices end with one merge.

**When it cannot.**
- **The push is rejected because the base moved.** It is retried on the next pass and counts as nothing.
- **A conflict.** The Task goes back to Ready with a comment to merge `<remote>/<base>` into its branch and resolve the conflict. The fixed branch is reviewed again. A second conflict on the same Task, counted from the synced tags of every device, assigns the Task to the person.
- **Anything else** (no remote, authentication, network) counts toward `dispatch.failure_limit`, and the Board's strip says why.

**No tests at deploy.** The review tested the branch, and the project's CI tests the push.

## Rejected alternatives

- **The reviewing agent merges and pushes in its own session.** Its push depends on what the client's sandbox allows: Codex has no network and a read-only `.git` by default. A merge decided by a model is also less predictable than a fixed git sequence.
- **Merging into the local base branch.** The base may be checked out in the person's folder, here shared by several sessions with uncommitted work; moving it under them leaves their index behind. Pushing to the remote from a throwaway worktree touches none of it.
- **Keeping a passed Task in Review until it is deployed.** Done would then mean "merged". But on a setup where no device deploys, every passed Task would wait in Review. The owner chose Done plus a note (2026-11-28).
- **Rebasing the branch onto a moved base.** It rewrites the reviewed commits. A merge commit keeps them as reviewed.
- **Running the project's tests before pushing.** There is no per-project test command. A failing CI run shows on the push, and a per-project command can be added later.

## Consequences

- A device switched to Review and Deploy brings Dispatched work to the base branch without the person: from Ready, through Review, to pushed.
- A pushed merge sets off the project's CI. A semantic conflict that merges cleanly is caught there, not before.
- The merge commit carries the repository's git identity: the person's, on the person's devices.
- Two conflicts on one Task bring the person in, as two failed reviews already do ([ADR 0234](0234-the-dispatcher-counts-review-rounds-and-failures-across-paired-devices.md)).
- The end-of-run tags gain the deploy role and its outcomes.

## Invalidation conditions

- If pushes to the base often fail CI after a clean merge, deploy gains a per-project test step before pushing, and this record is amended.
- If two deploying devices regularly race (rejected pushes on most passes), deploying takes a claim like a session does.
- If a project needs a deploy beyond a merge and push (a release, a tag, a command), that is a new record; this one covers only the merge.

## Validation

Proposed; not yet implemented. To be validated by T-k9gjm, with tests against a bare remote: fast-forward, merge commit, already merged, conflict then a second conflict, two deployers racing, and the default branch refused without `dispatch.deploy_branches`.
