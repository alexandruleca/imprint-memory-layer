---
name: imprint-tasks
description: Imprint's Board of Tasks, shared by every agent, client and paired device. Use when the user mentions the Board or a Task (T-xxxxx); before you claim, checkpoint, hand off or finish tracked work; when you finish a request and may take the next Task; to message another agent or client; and when a repository's issue tracker is the Imprint Board.
---

# The Board

Each Imprint workspace has one Board of Tasks. Claude Code, Codex and the other clients on every paired device read it with the `tasks` tool and change it with `task_edit`. A Task has a status (Backlog, Ready, In progress, Review, Done, Cancelled), a project, a priority from P0 to P3, acceptance criteria, tags and assignees, and may belong to a Feature: a Task that groups the Tasks building one piece of work, whose status follows theirs.

## Your name

Before your first claim or comment in a session, choose the name you go by on the Board: `task_edit(action="name", name="…")`, 2 to 24 letters, digits and hyphens. Pick something memorable; others address you as `@name`. An agent that claims without one gets a fallback such as `codex-k3fz`.

## Working a Task

1. **Claim** it: `task_edit(action="claim", task="T-…")`. Keep the claim token from the reply in this conversation. If your session restarts, take the claim back with `task_edit(action="claim", task="T-…", token="…")`. A Task held by a live session is refused: choose another.
2. **Re-check** the claim before long work: `tasks(action="get", task="T-…")` shows you as its holder. Of two claims made on two devices within the same minute, the earlier one holds; a notice on your next Imprint reply tells you if you lost yours. Stop working on that Task at once.
3. **Branch**: work on `task/<id>-<slug>`, for example `task/7f3kq-retry-uploads`. Commit and push as far as the repository and the user already allow. Other sessions may work in the same checkout: stage and commit only the files you changed (`git commit --only <paths>`), and leave the rest as you found it.
   When the Task builds on another Task's work, check that work is in your branch first: Done means its review passed, and its branch may still wait to be merged. When it is missing, comment to `user` naming what must land, and work on something else meanwhile.
4. **Checkpoint** at each meaningful step and at least every 30 minutes: `task_edit(action="checkpoint", task="T-…", text="what is done", next_step="what comes next", branch="…", commit="…", files=[…])`. A claim with no progress for 60 minutes is Stalling; at 120 minutes it is Stale, and the next agent takes it over from your last checkpoint. The next agent's Handoff reads the branch and commit from your last checkpoint, so when a commit's hash changes (a rebase, an amend, a rewritten history), record a new checkpoint with the new one.
5. **Finish**: when every acceptance criterion holds, record a checkpoint saying how you verified each one, then `task_edit(action="move", task="T-…", status="Review")`. The user, or a different session that reviews the work, moves it to Done. Tasks it blocks start once it is Done, so tell the user which ones wait on its review. To stop before finishing, checkpoint where you stopped and `task_edit(action="release", task="T-…")`; it goes back to Ready.

The Task is finished when it is in Review and its last checkpoint accounts for every acceptance criterion.

## Dispatched sessions

If your prompt says you are a Dispatched session, Imprint's Dispatcher started you for one Task: follow the `imprint-dispatch` skill, which your prompt carries, rather than this one. Tasks a Dispatched session creates start in Backlog.

## Taking work unasked

Once you have finished what you were asked, or during a run the user started to work the Board, take one Task at a time:

1. `tasks(action="next")` offers the Task you may take: Ready or Stale, in your project, standalone (neither a Feature nor one of a Feature's Tasks), unblocked, and unassigned or assigned to your client or device. The workspace's autonomy (`off`, `stale` or `ready`) decides what it offers; when it offers nothing, report that and stop. Take a Feature's Tasks only when the user asks for that Feature: `tasks(action="next", feature="T-…")` lists the ones open to take, in order.
2. Claim it, and say so in your reply and in a comment on the Task.
3. For a Stale Task, start from `tasks(action="handoff", task="T-…")`: the last checkpoint, the branch and commit, the files touched, and the previous holder's conversation, which `read_source` opens when Imprint has learned it.
4. Work it as in [Working a Task](#working-a-task).

## Creating Tasks

Before starting work the user describes, look for a Task that already covers it, in any status: `tasks(action="list", text="…")`. Another session may already be designing or building it.

Create a Task for work you find and cannot finish in this session: a leftover, a follow-up, a defect outside your scope. `task_edit(action="create", title="…", description="…", tags=[…])` puts it in Backlog. Put work in Ready when the user asks, with acceptance criteria: `acceptance=[…]`, `status="Ready"`, each criterion one checkable statement of at most 500 characters. Group the Tasks of one piece of work under a Feature and record their order with `blockers=["T-…"]`: create the Feature with `task_edit(action="create", title="…", kind="feature")`, then each Task with `feature="T-…"`. A Feature's status follows its Tasks: Ready once one is Ready, In progress once one starts, Done when every Task not Cancelled is Done. Don't move it or claim it; the Board refuses both. Only cancelling it is set by hand. A Task held by a claim cannot become a Feature.

## Talking to others

Comment on the Task the message is about: `task_edit(action="comment", task="T-…", text="…", to=[…])`. An address is `user`, `holder`, an agent's name, a client (`codex`, `claude-code`), `@device` or `client@device`; `@name` in the text addresses too, and a comment with no address goes to the Task's holder. Comments addressed to you arrive once as a notice: in Claude Code after your next tool call, and in every client on your next Imprint reply. `tasks(action="inbox")` lists the unread ones. A Task assigned to `user` belongs to the user: comment on it, and leave it unclaimed.

- **A comment from the user outranks your plan.** Act on it and reply on the Task before going on. When it says stop or hold, record a checkpoint and release the Task.
- **To reach the user** for a decision, a review or a check only they can run, comment to `user`: the comment stays on their dashboard's Needs-you list until they answer.
- **When your work changes what another Task builds on**, comment on that Task with what it inherits: the branch and commit, and the names and behaviour it can rely on.

## Reading the Board

`tasks()` shows the Board by status. `tasks(action="list", …)` filters by `project`, `feature`, `tag`, `status`, `priority`, `assignee` and `text`. `tasks(action="history", task="T-…")` shows every change to one Task. `tasks(action="progress")` says how far each project and Feature is and, from the pace of the last 14 days, how long is left: use it when the user asks how much remains.

## When the repository's issue tracker is the Board

Read [issue-tracker.md](issue-tracker.md) when a skill such as to-tickets, triage, wayfinder or implement-spec works with "the configured issue tracker" and the repository's tracker is the Imprint Board, or when the user asks to make the Board a repository's tracker.
