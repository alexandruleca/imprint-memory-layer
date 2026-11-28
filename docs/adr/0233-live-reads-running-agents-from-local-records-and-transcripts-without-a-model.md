---
title: ADR 0233 — Live reads what running agents do from local records and transcripts, by rule and without a model
description: The dashboard's Live view joins the Imprint servers' session records, the Board, the Dispatcher and the tails of the clients' own transcripts to show what each running agent on this device is doing, with tokens and context, using rules for activity labels, reading only new transcript bytes while asked, and calling no model.
---

- Status: Accepted
- Date: 2026-11-28
- Related: [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (the session records this builds on), [ADR 0151](0151-agent-sessions-page-by-last-activity-with-a-cursor.md) (the session history), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (claims and the Board's session), [ADR 0220](0220-a-dispatcher-starts-agent-sessions-for-board-tasks-when-the-person-switches-it-on.md) (Dispatched runs and their roles), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redaction), [dashboard](../dashboard.md)

## Context

The owner asked on 2026-11-28 for a main view of the active agents: what each is doing (building, reviewing, on which Task), its tokens and what else is available, "without increasing inference usage". The Agents page listed sessions and their Imprint tool calls ([ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md)), which say nothing about an agent's own edits, commands or tokens.

What is already on the device, checked that day:

- **Session records** (`agent_activity`): client and version, folder, workspace, process, the agent's Board name and Board session, and every Imprint tool call.
- **The Board** gives the Task a session holds, its status, Stalling and Stale, and the last checkpoint. **The Dispatcher's runs** record their role (work, review, takeover), session and conversation.
- **Claude Code transcripts** record, per reply, the model and usage (input, cache write, cache read, output, thinking tokens) and every tool use with its input. Each content block is a separate line repeating its reply's usage: in one transcript, 422 assistant lines carried 189 message ids, and all 233 repeats had identical usage. Replies end with `stop_reason` `end_turn` or `tool_use`.
- **Codex rollouts** record `task_started` and `task_complete` per turn, `token_count` events with total and last usage, the model's context window (475,000 for gpt-6.2-sol) and rate-limit use (the weekly window's percent), and tool calls, including code-mode `exec` calls whose code calls `tools.exec_command` or `tools.mcp__imprint__…`.
- **Finding the transcript.** The Board session is `conversation:<id>` only when the client passes its conversation id. The owner's WSL launcher predated forwarding it, so every Claude Code session there was `process:<id>`. The SessionStart and Stop hook payloads carry the transcript path. Measured on the owner's device, each live session's transcript wrote its first record 7 to 168 seconds after its Imprint server started, and two servers' nearest transcript was the same file.

## Decision

**Live** answers, for this device, who is working on what. It is computed when asked (`GET /api/agents/live`) and calls no model or provider.

**Sources.** It joins the running session records (one per Board session, the newest process), the Board (the held Task, its role and last checkpoint's next step), the Dispatcher's unfinished runs (a Dispatched session's role) and the session's transcript. Paired devices' agents appear only as the Board syncs them: holder name, client, device, Task and checkpoint.

**Finding the transcript.** In order: the client's conversation id (the path a hook recorded, else Claude Code's `projects/<folder>/<id>.jsonl`). Then the path a SessionStart hook recorded for the same client and folder within 5 minutes of the session's start. Then the transcript (Claude Code's project folder, or Codex's rollouts of the days around the start) whose first record is nearest the start within 5 minutes. Each transcript goes to one session only, nearest first. A WSL session served by a Windows runtime is found in its distribution's home through `\\wsl.localhost`, and its folder is named by its Linux path. The SessionStart and Stop hooks record the path they receive with one small write, so sessions are matched exactly from then on.

**Reading.** Only the bytes added since the last read are parsed, and a line still being written waits for the next read. A first read of a file over 32 MB starts that far from its end and marks its token totals partial. The state lives in the dashboard process; a transcript nobody asked about for 30 minutes is dropped. Nothing from a transcript is stored or sent.

**Activity, by rule.** The newest tool call without a result decides the label. Edit, Write and apply_patch read as Editing *file*. Build commands (npm/pnpm/yarn build, next/vite/astro build, tsc, go/cargo build, make, gradle, mvn, docker build) read as Building. Test commands (pytest, unittest, playwright, jest, vitest, go/cargo test, npm test) read as Testing. Any other command is Running. Read, Grep and Glob are Reading code, WebFetch and WebSearch are Searching the web, Task is Delegating. Imprint's tools are Searching Imprint, Saving to Imprint or Updating the Board. AskUserQuestion is Asking you, which counts as waiting.

A call without a result after 60 seconds is flagged: it may be waiting for approval, which a transcript cannot show. A turn with nothing running is Thinking. After a turn ends, the agent is Waiting for you for 15 minutes, then Idle. A session without a transcript shows a recent Imprint call (under 60 seconds) as its activity, else Idle. Commands and files are shortened to 80 characters and redacted.

**Tokens.** Context in use (the last reply's input with cache, or Codex's last input), the model's window where the client reports it (Codex), the session's new input, cached input, output and thinking, the cache share, Codex's rate-limit use, and today's tokens per client on this device. No prices.

## Rejected alternatives

- **Summarize each agent with a local model.** It would read naturally, but it adds inference on every refresh, which the owner ruled out. The checkpoint the agent writes and the rule-based labels carry the same facts.
- **A hook on every tool call** (Claude Code's PreToolUse/PostToolUse) reporting the current action. It would be exact, but it starts a process per tool call. [ADR 0212](0212-agents-talk-through-comments-on-tasks.md) kept the per-prompt hook to sourced shell for that reason. The transcript already holds the same events.
- **Re-reading whole transcripts each refresh.** The first Live read on the owner's data, 11 sessions over `\\wsl.localhost` including an 11 MB transcript, took 1.48 s on Windows; reading only the new bytes, the next took 0.17 s.
- **Trusting the conversation id alone.** On the owner's device every WSL Claude Code session lacked it, so Live would have shown nothing until the launcher was rewritten.

## Consequences

- The Agents page can show what each local agent does and spends without any model call. The cost is file reads while someone watches.
- Activity labels are only as good as the rules. A command that builds and tests at once reads as Testing. A permission prompt and a long command look the same, and the label says so.
- Matching by start time can pick the wrong transcript when two sessions start in one folder within seconds of each other and neither has a recorded path. The hooks remove that for new sessions.
- Live depends on the clients' transcript formats. A change there degrades a session to its Imprint calls rather than failing Live.

## Invalidation conditions

- If Claude Code or Codex stops writing tool calls or usage to their transcripts, or exposes the same through an API that costs nothing, revisit how Live reads them.
- If an Imprint-provided per-tool hook becomes cheap (no process per call), reconsider it as the source of the current action.
- If a client starts reporting its model's context window, show that window instead of none.
- If the owner wants other devices' live activity, a separate decision must cover syncing it, which this record does not.

## Validation

- `tests/test_agents_live.py` covers: each label rule, including Codex code-mode commands and apply_patch, and command redaction. Claude Code turns (running tool, Thinking, Waiting for you, Idle, an interrupted turn, a question). Tokens counted once per reply. Codex turns, tokens, window and rate limit. Tail reads of new bytes only, a half-written line, a shrunk file and a large file. `live()` with the Board, a Dispatched review, one-to-one start-time matching, a Codex hook path, a session without a transcript, paired devices' holders, and no model calls. WSL folders. The hook's record. Ran on Linux (repo venv) and Windows (the install's venv, Python 3.13) on 2026-11-28.
- On a copy of the owner's data on Windows (11 sessions across two projects, WSL clients), Live found every Claude Code and Codex transcript, named both projects, showed this session working on its Task, and read 1.48 s cold and 0.17 s after.
- Not yet run: a session started after the hook records its path (needs the updated install), and macOS.
