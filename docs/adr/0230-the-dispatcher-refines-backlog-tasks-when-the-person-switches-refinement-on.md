---
title: ADR 0230 — The Dispatcher refines Backlog Tasks when the person switches refinement on
description: With its own switch, off by default, the Dispatcher starts refinement sessions that groom Backlog Tasks without acceptance criteria, reading a throwaway copy of the project, and leave each Task in Backlog for the person to move.
---

- Status: Accepted
- Date: 2026-11-28
- Amends: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) — its "What it takes, in order": Backlog refinement after reviews and work, under a switch of its own
- Related: [ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md) (dispatched work feeding the Dispatcher), [ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md) (worktrees), [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (the built-in skill and forks), [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) (clients)

## Context

The owner asked (2026-11-28) that the Dispatcher's loop "also dispatch enhancement agents that look at the backlog and analyze tasks to add acceptance criteria and other things". Asked how, the owner chose:

- refined Tasks stay in Backlog;
- refinement takes Backlog Tasks without acceptance criteria;
- it has a separate switch and runs after work;
- the refiner may read the code, split large Tasks, flag duplicates and work that looks done, and set priority and tags.

Two facts about the Board and the Dispatcher shape the design:

- **Backlog and Ready.** Backlog holds work that is captured but not yet described well enough to start. A Task moves to Ready only with acceptance criteria ([ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md); [`changes.py`](../../imprint/tasks/changes.py), `move`).
- **Claims change status.** A claim makes a Backlog Task In progress. A release without a status puts a Task with criteria in Ready ([`claims.py`](../../imprint/tasks/claims.py), `release`).

## Decision

**The switch.** "Refine Backlog" is switched per device and workspace, is off by default, and is device-local. It sits on the Board's Dispatcher strip, in `imprint tasks dispatch refine on|off`, and in `PUT /api/tasks/dispatch` (`refine`). As for the Dispatcher's own switch, only the person turns it on, and the CLI refuses inside a Dispatched session ([ADR 0229](0229-dispatched-sessions-cannot-switch-the-dispatcher-on-or-start-more-sessions.md)). It works whether or not the Dispatcher's main switch is on.

**When.** Only in a pass that started no review and no work. It uses the same `dispatch.concurrency`, `dispatch.daily_limit` and failure limits as the rest.

**Which Tasks.** A Backlog Task qualifies when all of these hold:

- it has no acceptance criteria;
- it is not a Feature, not assigned to the person, not held, and not assigned to another device;
- its project has a folder on this device;
- no Dispatched refinement has started on it since the person's last change to it (an edit, move, comment, assignment, tag or link).

So each Task is refined once until the person changes it. Tasks that Dispatched sessions created are included.

**The session.** A refinement session has the role "refine" and runs as follows:

- **Where it reads.** A throwaway detached worktree of the project's checked-out commit, outside the project, with no branch. The worktree is removed when the session ends.
- **Its claim.** It claims the Task like a worker. When it ends, the Dispatcher releases the claim back to Backlog, and moves a Task the session moved on back to Backlog.
- **What it does.** The `imprint-dispatch` skill's "Refining" part tells it to:
  - read without changing files;
  - set checkable acceptance criteria, a fuller description, priority and tags;
  - split a large Task into Backlog Tasks of the same Feature;
  - flag duplicates and work that looks done by a comment, never cancelling;
  - end with a comment to the person.

**Outcomes.**

- `refined`: the Task gained acceptance criteria, or the session commented on it.
- `failed`: anything else, including a Task the session moved on. A failed refinement counts toward the guards of [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md).

**Never fed.** A refinement's claim and its release back to Backlog do not take the Task out of Backlog, so the guard of [ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md) does not count them. A refined Task is worked on only once the person moves it to Ready.

## Rejected alternatives

- **Moving a refined Task to Ready.** The owner chose to keep it in Backlog. Moving it would let dispatched work feed the Dispatcher ([ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md)), and the person would never look before it is worked on.
- **Moving to Ready only Tasks the person tagged first.** This was offered; the owner chose the simpler rule.
- **Refining every Backlog Task, or only tagged ones.** Every Task would spend sessions on Tasks that already have criteria. Tagged ones only would ask the person to mark each Task first. The owner chose Tasks without criteria.
- **Sharing the Dispatcher's switch, or giving refinement a limit of its own.** One switch would start grooming whenever the Dispatcher is on. A limit of its own would add a setting the owner did not ask for. The owner chose a switch of its own, sharing the limits.
- **Refining in the project's folder, or on a task branch.** The project's folder is the person's checkout ([ADR 0223](0223-dispatched-sessions-work-in-a-worktree-outside-the-project-and-never-push-or-merge.md)). A task branch would leave an empty branch behind for every Task refined.
- **A read-only client mode** (Claude Code's plan mode, Codex's read-only sandbox). Neither is tested for the Board writes a refinement must make through Imprint's tools. The runners' tested safe modes, in a throwaway copy, keep any stray edit away from the person's files.

## Consequences

- Backlog Tasks without criteria get criteria and context while nothing else waits, and the person decides what becomes Ready.
- Refinement spends sessions out of the same daily limit as work.
- A refinement may add Backlog Tasks (splits). They wait for the person as well.

## Invalidation conditions

- If the person moves most refined Tasks to Ready unchanged, offer to move them to Ready automatically behind a setting, through a record that amends [ADR 0227](0227-dispatched-work-never-feeds-the-dispatcher-until-the-person-moves-it.md).
- If refinements crowd out work within the daily limit, give refinement its own limit.
- If a client gains a tested read-only one-shot mode that still allows Imprint's tools, use it for refinement.

## Validation

- `tests/test_dispatch_refine.py` covers:
  - the choice of Tasks, and refinement only after reviews and work, under its own switch;
  - a refined Task staying in Backlog with no branch made and its copy removed;
  - refining once until the person changes it;
  - a Task moved on going back to Backlog;
  - a refined Task the person readies being worked on;
  - refusing a Task not in Backlog, and the shared daily limit.
- `tests/test_dispatch_loop.py` and `test_dispatch_api.py` cover the switch.
- `tests/test_builtin_skills.py` covers the skill.
- Linux (WSL Ubuntu 26.04, Python 3.12 venv): the `test_dispatch_*` modules pass.
- Not run: a refinement by a real Claude Code or Codex session.
