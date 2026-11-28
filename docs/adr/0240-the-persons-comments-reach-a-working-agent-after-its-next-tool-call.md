---
title: ADR 0240 — The person's comments reach a working agent after its next tool call
description: One registry of notices, written per conversation and printed by a sourced shell hook after every tool call as well as on every prompt, brings the person's comments to the agent holding the Task mid-turn; claims and Dispatched prompts carry the comments a new holder has not read, and the built-in skills rank them above the agent's plan.
---

- Status: Accepted
- Date: 2026-11-28
- Scope: The registry, the files and the hooks after each tool call (T-31w4v); claims, Dispatched prompts and the skills' rule come with T-7dnj3.
- Amends: [ADR 0212](0212-agents-talk-through-comments-on-tasks.md) — its "Delivery" and "The prompt hook": a notice also after each tool call, from one registry, refreshed within seconds of a poke
- Related: [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (Dispatched sessions), [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (built-in skills), [ADR 0236](0236-cursor-gets-imprints-hooks-wherever-it-runs-and-one-hook-answers-each-event.md) (Cursor runs Claude Code's hooks)

## Context

The owner asked (2026-11-28): "If I leave a comment on a task, the agent that is working on it should get notified."

- A comment addressed to nobody already goes to the Task's holder ([`comments.py`](../../imprint/tasks/comments.py), `_addressed`). It reaches the holder on its next Imprint tool reply, at session start, or in Claude Code on the next prompt ([ADR 0212](0212-agents-talk-through-comments-on-tasks.md)).
- A busy agent in the middle of a turn calls many tools between prompts, mostly not Imprint's. A Dispatched session (`claude -p`, `codex exec`) has a single prompt. Either can work for a long time with the person's comment unread.
- A comment on a Task nobody holds reaches nobody. The next session to claim it is not told, and a Dispatched prompt ([`dispatched.py`](../../imprint/dispatch/dispatched.py), `prompt`) carries no comments.
- The prompt hook's file is a finished UserPromptSubmit reply ([`tasks_tool.py`](../../imprint/tasks_tool.py), `refresh_prompt_notices`), written on each lease renewal, every 60 seconds.
- Claude Code (hooks reference, 2026-11-28):
  - a PostToolUse hook may return `hookSpecificOutput.additionalContext`, "added to Claude's context alongside the tool result";
  - with no matcher it fires on every successful tool call, MCP tools included;
  - matching hooks block until they finish;
  - hooks run under `claude -p` unless `--bare`.
- Codex fires PreToolUse and PostToolUse on Bash only ([`setup_codex.go`](../../cmd/setup_codex.go)).
- Another session (imprint-memory-layer-42) is building messages to any agent session for the Agents page. It agreed to deliver them through the same files.

## Decision

**After every tool call.** Claude Code gets a PostToolUse hook with no matcher beside the prompt hook. Both run the same sourced shell script within ADR 0212's 100 ms budget, starting no process when nothing waits. Codex gets none: see the rejected alternatives. Cursor also runs Claude Code's hooks ([ADR 0236](0236-cursor-gets-imprints-hooks-wherever-it-runs-and-one-hook-answers-each-event.md)), and the notice hook needs no Cursor twin: no Cursor session has a notice file, so there it prints nothing.

**One registry of notices.** Providers contribute (keys, lines, heading) and set their own limits:
- Board comments are the first provider (`read:<event id>`);
- the other session's per-session messages are the second (`msg:<id>`).

Settle handlers, registered per key prefix, are called as `settle(keys, session, via, at)`, with `via` one of prompt, tool, reply or inbox. The whole text is capped at 8,000 characters. Imprint tool replies gather from the same registry, so every client sees the same notices.

**The files.** The conversation's Imprint server is their only writer.
- `<conversation>.notice` holds the text as one JSON string.
- `<conversation>.notice-keys` holds its keys, written after the notice.
- The hook command passes its event name. The script prints the hook reply around the text, takes the file away, and writes `<conversation>.notice-by` naming the event.
- The server refreshes the files on each lease renewal and within 2 seconds of a newer `<conversation>.poke`. Adding a comment touches that file for the holder's conversation and for each conversation it is addressed to.

**A new holder.** A claim reply carries the person's comments on the Task that the claiming session has not read. A Dispatched prompt carries the person's comments since the Task's last Dispatched run ended.

**What the agent does.** The built-in `imprint-tasks` and `imprint-dispatch` skills say:
- a comment from the person overrides the agent's plan;
- the agent acts on it and replies on the Task before going on;
- "stop" or "hold" means checkpoint and release.

## Rejected alternatives

- **Claude Code's cross-session inbox socket for comments.** The other session verified that it delivers at once. But Claude Code frames such a message as coming from "another Claude session, not typed by your user", so the person's comment would arrive with less authority than it has. It stays the path for a session idle at its prompt, which no hook reaches.
- **A second, pre-rendered file per hook event.** Whichever hook ran first would print its file, and the other would print the same notice again. One file, with the event supplied by the hook, is printed once.
- **Writing a user message into a Dispatched Claude Code session's stdin (`--input-format stream-json`).** The documentation does not say whether a message written mid-turn is read before the turn ends, and Codex has no equivalent.
- **Keeping delivery to tool replies, session start and prompts.** That is today's behaviour, which the owner's request rejects.
- **A Codex PostToolUse hook.** Codex's hooks do pass a PostToolUse hook's `additionalContext` to the model (`codex-rs/hooks/src/events/post_tool_use.rs`, 2026-11-28). But Codex gives its thread id only to the shell commands it runs (`CODEX_THREAD_ID`, `CODEX_SESSION_ID` in `codex-rs/core/src/exec_env.rs`), not to MCP servers. Imprint's Codex server therefore knows the session only as its process, and has no per-conversation notice for a hook to print.

## Consequences

- The person's comment reaches a working Claude Code agent after its next tool call, at most about 2 seconds after the comment is posted.
- Every tool call in Claude Code runs one more sourced hook. The empty case is one file test, measured against the 100 ms budget.
- Codex sessions without a usable PostToolUse hook still see comments on Imprint tool replies and at session start.
- Delivery records `via` and `at`, so the Agents page can show how and when a message arrived.
- Files written in ADR 0212's format by servers that have not restarted are swept after a day.

## Invalidation conditions

- If the hook's empty case costs more than 100 ms per tool call on the owner's machines, the PostToolUse hook is removed and delivery falls back to ADR 0212's paths.
- If Claude Code lets an outside process add a user-framed message to a running session, that path may replace the PostToolUse hook.
- If Claude Code stops passing PostToolUse `additionalContext` to the model, or does not pass it in `-p` mode, Dispatched sessions fall back to tool replies, and this record is amended.

## Validation

T-31w4v, 2026-11-28:
- **A real Claude Code session** (`claude -p`, Claude Code 2.1.332, Haiku), with the repository's MCP server on a scratch data folder and only the new PostToolUse hook added:
  - it claimed a Task, then ran `sleep 6` four times and called no other Imprint tool;
  - a watcher posted the person's comment once the claim was recorded;
  - the session's transcript shows a `hook_additional_context` attachment from `PostToolUse:Bash` carrying the comment, and the model's request carrying "PostToolUse:Bash hook additional context";
  - the agent quoted the comment in its answer.
  So a PostToolUse hook's context reaches the model in `-p` mode too.
- **Cost per tool call**, median and 90th percentile of 30 runs:

  | Path | Nothing waits | A notice waits |
  | --- | --- | --- |
  | Native Linux, sourced as Claude Code runs it | 3.8 / 4.4 ms | 8.6 / 9.6 ms |
  | WSL profile's bridge script, Windows data folder on `/mnt/c` | 12.1 / 13.9 ms | 37.9 / 43.3 ms |

- **Tests** (`tests/test_prompt_notices.py`, 13 tests in sh and bash):
  - after a tool call the hook answers as PostToolUse, once, by either hook;
  - an unknown event prints nothing;
  - the server records which hook told a notice and when;
  - a person's comment on a held Task pokes its conversation;
  - the cap and the pointer, and keys of unknown prefixes kept;
  - files of earlier releases are swept.
- **Go tests:** the hook command prints each event's reply once and leaves `.notice-by`, setup writes both hooks and withdraws them, and the WSL profile writes both through its script. `test_hooks`, `test_tasks*`, `test_mcp*` and the Go `cmd` and `internal` packages pass on Linux; the full Linux suite passes (1,835 tests). Natively on Windows, `test_prompt_notices` passes in Git Bash (the timing test is skipped there), as do `test_hooks`, `test_tasks*`, `test_mcp*` and the Go hook tests.

Not run: the hook's cost under Git Bash on native Windows, and Codex, which gets no hook (see the rejected alternatives; T-ja5sb).
