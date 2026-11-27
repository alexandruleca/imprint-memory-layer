---
name: imprint-dispatch
description: Instructions for a Dispatched session, an agent session that Imprint's Dispatcher started without the person to work on one Task of the Board. Use only when your prompt says you are a Dispatched session; an interactive session uses imprint-tasks instead.
---

# You are a Dispatched session

Imprint's Dispatcher started you to work on one Task of the Board. Nobody is watching this run and nobody will answer a question: decide what you can from the Task, the repository and Imprint's memory, and record what you could not decide on the Task. Your prompt names the Task, the workspace, the project, your worktree, its branch and base, and your claim token.

## Rules

- **Pass the workspace** your prompt names on every Imprint call (`workspace="…"`), and the project when you create a Task (`project="…"`).
- **Work only in your worktree.** Never change, check out or reset the project's own folder or another worktree. Commit on your branch only.
- **Never push, merge, rebase onto another branch, tag or open a pull request.** The person merges your branch after a review.
- **One Task.** Do not take other Tasks. A Task you create for work you found goes to Backlog; never move it to Ready.
- **If you lose your claim, stop at once.** An Imprint reply that says you lost it, or `tasks(action="get")` showing another holder, means another session works on this Task.
- Your client's safety checks stay on. Do not try to get around a refused action: record it on the Task and continue without it, or stop.

## Steps

1. **Name yourself**: `task_edit(action="name", name="…")`.
2. **Take over the claim** the Dispatcher made for you: `task_edit(action="claim", task="T-…", token="<your claim token>")`. If that is refused, stop.
3. **Read the Task** with `tasks(action="get", task="T-…")`: its description, acceptance criteria and comments. If it was worked on before, read `tasks(action="handoff", task="T-…")` and continue from the last checkpoint. Comments from an earlier review list what to fix.
4. **Search Imprint** for earlier decisions and work on this project before reading files.
5. **Set up the worktree** if the project needs it (dependencies, a virtual environment): it starts as a clean checkout.
6. **Work, committing on your branch.** Follow the repository's own instructions (CLAUDE.md, AGENTS.md, its contributing docs). Record a checkpoint at each meaningful step and at least every 30 minutes: `task_edit(action="checkpoint", task="T-…", text="what is done", next_step="what comes next", branch="<your branch>", commit="<last commit>", files=[…])`.
7. **Finish**: when every acceptance criterion holds, record a checkpoint that says how you verified each one, then `task_edit(action="move", task="T-…", status="Review")`. A different session reviews it; you never move it to Done.
8. **If you cannot finish**, record a checkpoint saying where you stopped and why, comment to `user` with what you need from them, and end the run. The Dispatcher releases the claim.

End your run once the Task is in Review or you have recorded why you stopped.
