---
title: ADR 0077 — The Stop hook hands its turn to one waiting learner under the queue lock
description: The Stop hook starts a detached learner and returns; at most one learner per conversation waits, it lets a burst of turns settle, then learns in one process while holding the queue lock.
---

- Status: Accepted
- Date: 2026-10-26
- Amends: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) — the Stop hook's learning takes the queue lock instead of running alongside jobs, and waits for it instead of handing over to the dashboard
- Related: [ADR 0003](0003-local-mcp-and-portable-hooks.md) (portable hooks), [ADR 0026](0026-windows-installer-wsl-client-bridge.md) (WSL hooks run the Windows runtime), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (the privacy filter loads per process), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (learners stop background tagging), [ADR 0068](0068-merge-identical-waiting-jobs.md) (the dashboard's merging of identical jobs), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (a turn is learned in its session's workspace), [queue](../queue.md)

## Context

On 2026-10-26 a laptop running several Claude Code sessions in WSL2, with the Windows runtime at v0.24.0, ran short of memory. Three `imprint.cli_conversations --transcript` processes were learning the same transcript at once: 72 MB, 10,716 records. They had started at 11:38, 11:40 and 11:54, were using about 2.8 GB, 2.4 GB and 9.6 GB and still growing, and had together used about 4.5 hours of CPU time. The hook processes that started them had exited. On 2026-10-23 the kernel's OOM killer in the same WSL instance had killed a `python` process holding 10 GB of anonymous memory.

The Stop hook produced this:

- **Stop ends every turn, not a session.** Clients run it when they finish answering, including turns that background events start, and the sessions above were listening to such events.
- **Every turn read the whole conversation three times.** `hook_entry.py` ran `cli_conversations --transcript`, `cli_extract` and `cli_summarize` one after another with `subprocess.run`. Each read the whole transcript, and each process loaded the embedding model for its writes, and the privacy filter too when it is on (about 1.4 GB per process, [secret redaction](../redaction.md)). In v0.24.0 the conversation learner also ran the local LLM tagger on every exchange, so one run took many minutes. ADR 0067 has since moved that tagging to the background.
- **The client's timeout stopped the hook, not its learners.** The hook is registered with a 120 s timeout. Behind the WSL bridge the learners are Windows processes; they kept running without a parent while the next turn's hook started another set.
- **Nothing serialized them.** ADR 0064 kept hook work outside the queue lock ("hook summaries … run alongside a job"), so learners overlapped each other and other jobs.

What a run costs today was measured before deciding. On dev after commit 5683d08 (conversation chunks without model inference) and ADR 0067, reading that transcript's records takes 0.15 s with a 130 MB peak (WSL2), and `vectorstore.store_batch` embeds only chunks whose IDs are not stored yet. A run's cost is starting Python and loading the embedding model, not the size of the conversation; running several at once multiplies it.

## Decision

**The Stop hook starts one detached learner and returns.** Its `stop` action runs `python -m imprint.session_learn <transcript>` in a session of its own (Unix) or a process group of its own without a window (Windows), with no inherited standard streams, and returns `{}`. The session's workspace (ADR 0072) and the WSL bridge's path context reach the learner through its environment.

**At most one learner per conversation waits, and it waits for the queue lock.** The learner (`imprint/session_learn.py`):

1. Takes the conversation's waiting slot without waiting: a lock file in `data/learn_slots/`, named by a hash of the session's workspace and the transcript path. When another learner holds the slot, it exits. That learner has not read the transcript yet, so it learns this turn as well.
2. Waits 60 s (`SETTLE_SECONDS`), so a burst of turns is learned once.
3. Waits for the queue lock as a job the dispatcher started does: it never hands over to the dashboard, and it stops background tagging as any other taker does (ADR 0067). The lock names it `learn`. On Windows, where a blocking attempt gives up after about 10 s, it tries again.
4. Gives up its slot, removing the file, and only then reads the transcript. A turn that ends after that starts a learner that waits behind it.
5. Learns in one process: the conversation's exchanges (`cli_conversations.index_transcript`), decision extraction (`cli_extract.extract`) and, with `summarizer.enabled`, the summary. A step that fails does not stop the others. Output goes to `data/session_learn.log`, which starts over past 1 MiB.

## Rejected alternatives

- **Queue a `learn` job through the dashboard each turn, where ADR 0068 merges the duplicates.** The dispatcher runs only while the dashboard does, so without it nothing would run the jobs. With it, every turn would add a row to the queue's history and raise a job toast when it ended.
- **Learn only new exchanges from a per-conversation checkpoint.** Proposed in the diagnosis of the incident above. The measurement in *Context* showed that reading a 72 MB transcript is cheap and stored chunks are already not embedded again. A checkpoint would add per-conversation state, and the handling of an exchange that is still growing, without removing the start-up and model load that each run pays.
- **Learn at session end (SessionEnd) instead of after each turn.** A session that runs for days would go unlearned for days, and one that is killed would never be learned.
- **One waiting learner for all conversations, learning a shared set of pending transcripts.** It bounds waiting learners overall instead of per conversation, but needs a pending set and a protocol for claiming it. Per-conversation slots need only lock files, and the queue lock already lets one learner run at a time.
- **No settle period.** A session that wakes every few seconds would chain learners back to back, each holding the queue lock and loading the embedding model, and the dashboard's dispatcher, which starts a job only when no process holds the lock, would rarely find it free. This was reasoned from the dispatcher's code, not measured.

## Consequences

- A conversation has at most one learner waiting, a sleeping process with only the standard library and Imprint's settings loaded, and one Imprint job runs at a time, so a turn's learning never adds a second model to memory.
- A turn is learned at least 60 s after it ends, later when another job holds the lock. A learner does not need its hook or its client to keep running. If the computer shuts down first, `imprint learn` learns the conversation later, since its watermark is behind the file.
- While a learner runs, typically for seconds, other lock takers behave as for any job: a dashboard or MCP purge or move is refused, `imprint ingest` hands over to the dashboard or waits, and the Queue page shows `learn` as the active job.
- The registered hook timeout (120 s) no longer bounds learning; registrations stay as they are. The hook itself now takes as long as resolving the session's workspace and, behind the WSL bridge, the conversation's scope check.
- The learners' output, which went to the hook's standard error, goes to `data/session_learn.log`.
- Removing a slot's file lets a learner that opened the old file hold it while another holds a new one. That costs one repeated learn, never a lost turn.

## Invalidation conditions

- If a long-lived Imprint process comes to run on every install, the Stop hook can hand turns to its queue, and the slots go (ADR 0064 names the same condition for the lock file).
- If a run's cost comes to grow with the conversation rather than with start-up, for example because a step processes every exchange with a model again or transcripts grow by orders of magnitude, learn new exchanges from a checkpoint.
- If a client starts ending the processes its hooks leave behind, learners must be started outside the hook's process tree or by another process.
- If another session must see a turn within seconds of it ending, the settle period has to shrink or go.

## Validation

Executed on 2026-10-26:

- `tests/test_session_learn.py` (Linux, Python 3.12): a second learner exits while the slot is held, without settling or touching the queue lock; the slot is held through the settle period and the wait for the lock and is free (its file removed) before the transcript is read; a blocking attempt that gives up is repeated; the lock and slot are released when learning raises `SystemExit`; each workspace has its own slot; one process runs every step and a failing step stops none; the log starts over past its limit.
- `tests/test_hooks.py`, `tests/test_wsl_bridge.py` and `tests/test_workspace_directories.py`: the Stop hook starts one detached learner with no inherited standard streams and prints `{}`; a Copilot Stop payload starts it and `stop_hook_active` starts nothing; an unapproved WSL conversation starts no learner; the learner starts in the session's workspace.
- Across processes, running `session_learn.learn` with its learning steps replaced by a recorder, on Linux (WSL2) and natively on Windows 11 (build 26200, CPython 3.12.8, data directory on NTFS): seven Stops (a burst of five, one while the first learner learned, one while the second waited) produced two learns, one after the other, each the queue lock's holder, and left no slot files. On Windows a learner waited behind a process holding the lock for 15 s, past the 10 s a blocking attempt lasts, and learned 0.65 s after the lock was released.
- Through WSL interop, a Windows CPython started from WSL with its standard output piped, starting a child as the hook does, returned in 0.9 s; the child finished 15 s later, after its parent had exited.

Not validated: a learn started by a real client's Stop hook, since the WSL machine's Imprint runtime was uninstalled while this was written; macOS; Claude Code running natively on Windows; Codex and GitHub Copilot hooks; whether a native Windows client ends its hooks' detached processes when it exits.
