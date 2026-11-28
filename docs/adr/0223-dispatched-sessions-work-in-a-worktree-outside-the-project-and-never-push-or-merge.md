---
title: ADR 0223 — Dispatched sessions work in a worktree outside the project and never push or merge
description: Each Dispatched session works on its Task's branch in a git worktree that Imprint makes outside the project, starting from the branch checked out in the project; it commits there and never pushes, merges or touches the person's checkout, and merging stays the person's.
---

- Status: Accepted
- Date: 2026-11-27
- Related: [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (Task branches and pushing), [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (the Dispatcher), [ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md) (reviews), [ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md) (lost claims)

## Context

Dispatched sessions run while the person may be working in the same project, and while other agents work on other Tasks there.

- Agents work on a branch named `task/<id>-<slug>` and record it with its last commit in each checkpoint. A claim never grants permission to push. A takeover on another device starts from the last pushed state ([ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md)).
- Codex 0.171 has no option to make a worktree of its own (`codex exec --help`, 2026-11-27).
- In this repository, work happens on `dev` and `main` is the release branch, so the remote's default branch is not where new work starts.

## Decision

**A worktree outside the project.** Imprint makes a git worktree for each Task at `~/.imprint/worktrees/<project>/<task-id>`, on the system the project lives on (inside the distro for a WSL project). The Dispatched session works only there.

**The branch.** The worktree is on the Task's branch `task/<id>-<slug>`. The first dispatch makes the branch from the branch checked out in the project's folder at that moment, and records that base in the Task's first checkpoint, so a reviewer diffs against it ([ADR 0221](0221-dispatched-sessions-review-tasks-in-review-with-another-client.md)). Later dispatches of the same Task, after a failed review or a takeover, continue on the branch.

**Never push or merge.** Dispatched sessions commit on the Task's branch. They never push, merge, or change the project's own checkout. When a review passes, the Task's comment names the branch, and the person merges it.

**Takeovers from another device.** The Dispatcher takes over a Stale Task only when the Handoff's last commit exists in this device's copy of the repository, or the Task has no commit yet. Otherwise it leaves the Task to the device that has the work, and comments once to say why.

**Cleanup.** A worktree is removed when its Task reaches Done or Cancelled, or when its session loses the claim ([ADR 0224](0224-a-dispatched-session-starts-only-after-its-claim-has-reached-the-paired-devices.md)). The branch and its commits stay.

## Rejected alternatives

- **A worktree inside the project.** The project's test runners and linters, and Imprint's own ingest, would pick up a second copy of every file.
- **Claude Code's `--worktree` option.** Codex has none, and one mechanism keeps both clients' work in the same place.
- **Working in the person's checkout.** It collides with the person's own changes and with other sessions working in the same project.
- **Starting from the remote's default branch.** That is `main` here, while work happens on `dev`.
- **The reviewer merging a passing branch.** Merging stays the person's step: an unattended merge into the branch the person works on would surprise them.

## Consequences

- The person's checkout is never touched; dispatched work appears as branches to merge.
- A fresh worktree has no installed dependencies; the `imprint-dispatch` skill tells sessions to set them up there.
- Worktrees take disk space until their Tasks end.
- A Task can sit Done with an unmerged branch; its comment to the person says so.

## Invalidation conditions

- If the person regularly merges dispatched branches without changes, a new record may let a passing review merge into the base branch, behind a setting.
- If the base branch checked out at dispatch is the wrong one more often than the right one, add a base branch per project.

## Validation

- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the full suite, 1,691 tests, passed on 2026-11-27 with the Dispatcher's Tasks together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): the `test_dispatch_*` modules (121 tests), `test_tasks*`, `test_cli_tasks`, `test_sync_pairs`, `test_builtin_skills`, `test_config_help`, `test_adr_records` and `test_docs_tables` passed.
- `tests/test_dispatch_run.py`, `test_dispatch_takeover.py` and `test_dispatch_reviews.py`: the worktree outside the project on the Task's branch from the checked-out branch, the project's checkout untouched, a later dispatch continuing on the branch, a takeover continuing at the Handoff's commit or skipped with one comment when that commit is not here, and removal once the Task ends.
- Real runs: the worktrees were made under the Windows and the WSL homes, and the commits landed on the task branches.
