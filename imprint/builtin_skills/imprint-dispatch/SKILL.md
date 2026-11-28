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

## Reviewing

When your prompt says you are to **review** the Task, another session did the work and you check it. The Task stays in Review while you hold it.

1. **Name yourself** and **take over the claim** with your token, as above.
2. **Read the Task**: its acceptance criteria, the worker's checkpoints (how it says it verified each criterion) and the comments.
3. **Check the change** in your worktree, which is on the Task's branch: read `git diff <base>...HEAD`, with the base your prompt names, run the project's tests and checks as its own instructions say, and check each acceptance criterion yourself. Look for real defects; leave style preferences out.
4. **Do not change the work.** Reviewing is reading and running, not fixing; make no commits.
5. **Pass**: when every criterion holds and you found no defect, comment what you checked and how, then `task_edit(action="move", task="T-…", status="Done")`. The Dispatcher tells the person the branch is ready to merge.
6. **Fail**: comment your findings to `holder`, each with where it is (file and line) and what would fix it, then `task_edit(action="move", task="T-…", status="Ready")`. The next worker continues on the same branch from your comment.

## Refining

When your prompt says you are to **refine** the Task, it waits in Backlog without acceptance criteria, and you make it ready for the person to decide on. Your worktree is a throwaway copy of the project: read it, change nothing in it, make no commits. The Task stays in Backlog; the person moves it on.

1. **Name yourself** and **take over the claim** with your token, as above.
2. **Read the Task**, its comments and its Feature if it has one (`tasks(action="get", task="T-…")`), and search Imprint for earlier decisions about what it touches.
3. **Read the code** the Task touches in your worktree, so that what you write matches what is there. Run read-only commands only.
4. **Edit the Task**: `task_edit(action="edit", task="T-…", acceptance=[…], description="…", priority="P…")`. Write acceptance criteria someone can check. Keep the person's words in the description and add what you found: the files involved and how the code behaves now. Change the priority only when the Task's own words make it clear. Add or remove tags with `task_edit(action="tag", task="T-…", add=[…], remove=[…])`.
5. **A Task too large for one session**: create a smaller Task for each part, each with criteria, in Backlog and in the same Feature (`feature="T-…"` when it has one), then comment on the original which Task took which part.
6. **A duplicate, or work that looks done already**: comment to `user` naming the other Task or the code that does it. Never cancel or delete a Task.
7. **Never move a Task's status or release your claim**: the Dispatcher puts the Task back in Backlog when you end. Never edit files, commit or push.
8. **Finish** with a comment to `user` that sums up what you changed and what you could not decide, then end the run.
