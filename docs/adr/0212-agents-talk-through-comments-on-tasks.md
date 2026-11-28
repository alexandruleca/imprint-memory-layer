---
title: ADR 0212 — Agents talk through comments on Tasks
description: Messages between agents, clients and the user are comments on Tasks, addressed to a client, a device or the holder; they reach a running agent as a notice on any Imprint tool reply, at session start, and in Claude Code on every prompt through a new UserPromptSubmit hook that stays only while it adds less than 100 ms.
---

- Status: Amended
- Date: 2026-11-27
- Scope: The notice on tool replies, the inbox, the session-start context, the dashboard's notifications (a desktop notification only in the desktop app) and Claude Code's UserPromptSubmit hook, natively and in WSL profiles; on native Windows only where Git Bash is installed.
- Amended by: [ADR 0240](0240-the-persons-comments-reach-a-working-agent-after-its-next-tool-call.md) — its "Delivery" and "The prompt hook": a notice also after each tool call, from one registry, refreshed within seconds of a poke
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0003](0003-local-mcp-and-portable-hooks.md) (portable hooks), [ADR 0210](0210-claims-are-leases-held-by-the-agent-session.md) (holders)

## Context

The owner wants the Board to "facilitate communication between different harnesses such as codex and claude code" (2026-11-27).

- An MCP server answers tool calls; it cannot interrupt a running agent. Every client shows an MCP tool's reply to its agent.
- Claude Code's SessionStart hook adds context through `hookSpecificOutput.additionalContext`, and its PreToolUse hook for Read, Grep and Glob adds context once per session ([`hook_entry.py`](../../imprint/hook_entry.py); `cmd/setup.go`). No hook runs on each of the user's prompts today.
- Codex gets SessionStart and Stop only; its tool hooks fire on Bash only (`cmd/setup_codex.go`). Cline and OpenClaw get no hooks.

## Decision

**Comments only.** Agents, clients and the user talk through comments on Tasks. A comment can be addressed to a client (`@codex`), a device, the Task's holder, or the user. There is no channel outside Tasks, so every message has its work's context and stays in its history.

**Delivery.** Each agent session has its own read state. Unread comments addressed to a session, or to the Tasks it holds, reach it:

1. as a short notice ("2 new comments on T-7f3kq") added to any Imprint tool reply, in every client;
2. at session start, with the project's Ready and Stale Tasks, through the SessionStart hooks Imprint already installs;
3. in Claude Code, on every prompt, through a new UserPromptSubmit hook. The hook is kept only while it adds less than 100 ms to a prompt on the owner's machines.

Comments addressed to the user, and Tasks reaching Review, show the dashboard's toast and a desktop notification.

**The prompt hook.** The conversation's Imprint server already wakes every minute to renew its leases; on each pass it writes what it would announce to `data/prompt-notices/<conversation>.json`, already in the hook's output format, and the keys of those items beside it. The hook is a shell script ([`prompt_notices.sh`](../../imprint/prompt_notices.sh)) sourced in Claude Code's own hook shell: it reads the start of its input for `session_id`, and only when a file waits does it move it aside, print it and remove it. A keys file whose notice file is gone tells the server the hook printed it, and the server marks those items told; a tool reply first takes the file back, so nothing is told twice. In a WSL profile a script beside the bridge launcher runs the Windows installation's hook over the Windows data folder. On native Windows Claude Code runs hooks in Git Bash, or in PowerShell when Git Bash is missing, so setup registers the hook only where Git Bash is found.

## Rejected alternatives

- **A chat channel outside Tasks.** Weighed in the design discussion; messages without a Task lose their context and their record.
- **Delivery through hooks only.** Codex has no hook that runs during a session's turns, so it would see messages only at session start.
- **A Python prompt hook, like the other hooks.** Imprint's existing WSL-bridged hook took 140–160 ms on the owner's machine (2026-11-27), past the 100 ms limit, and a Python start is most of that.
- **Running the script with `sh` as a new process.** Under Git Bash each process costs tens of milliseconds: `sh` and `head` took the empty case to 174 ms through WSL interop, against 82 ms when sourced.

## Consequences

- A message waits until its recipient next calls Imprint or, in Claude Code, until the next prompt. It is mail, not chat.
- Codex sees new comments on Imprint tool replies and at session start, not on every prompt.
- Setup gains one Claude Code hook, which `imprint disable` and the cleanup paths also remove, with the WSL profile's `imprint-prompt` script.
- A notice can wait up to a minute before the prompt hook can carry it; a tool reply carries it at once.
- A conversation that ended leaves its file until a server sweeps files older than a day.

## Invalidation conditions

- If Codex or another client offers a hook that runs on each prompt and can add context, use it there.
- If the UserPromptSubmit hook adds 100 ms or more to a prompt on the owner's machines, remove it.
- If MCP clients let a server put a message in front of a running agent, deliver comments that way.

## Validation

- `tests/test_tasks.py`: comments kept with their author and addresses, mentions, delivery by name, client, device, client on device and holder (the holder when read), no notice of one's own comments, the user's inbox, and reserved names.
- `tests/test_tasks_tool.py`: a comment told once as a notice to the addressed client and then read in the inbox; a Stalling holder told once. `tests/test_hooks.py`: the session-start context with the project's Board, unread comments and the session's name, within its size.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,522 tests, passed on 2026-11-27 with the Board and built-in skills together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_builtin_skills`, `test_registry`, `test_registry_files`, `test_skills_tool`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 187 tests, passed.
- `tests/test_prompt_notices.py`: the hook prints a prepared notice once, under `sh` and sourced in `sh` and `bash`, with a 300,000-character prompt; what it printed is not repeated on a tool reply and a tool reply leaves no file; no file without news or a conversation id; malformed input is quiet; old files are swept. `internal/hooks`: the registered command prints once from an install path with spaces and an apostrophe. `cmd`: the hook only where a shell runs it, owned across relocation, withdrawn without Git Bash, removed by cleanup; the WSL profile's script, its registration and removal, and `sh -n` over the generated scripts.
- With the prompt hook: the full Linux suite, 1,554 tests, passed; on Windows 11 from a native copy, `test_prompt_notices` under Git Bash (6 tests, the timing test left to the figures below), `test_tasks`, `test_tasks_tool` and `test_hooks`, and the cross-compiled `internal/hooks` tests and `cmd` hook, WSL and cleanup tests, passed.
- Prompt hook time, 2026-11-27, median of 20 runs (10 on Windows) with a 200,000–300,000-character prompt: Linux (WSL Ubuntu, dash) 4.6 ms with nothing waiting and 10.5 ms when a notice waits. Windows 11 Git Bash, started from WSL: 82 ms with nothing waiting against 70 ms for `bash -c true` alone, so the hook adds about 12 ms; 178 ms when a notice waits (three more processes). Starting a Windows process through WSL interop costs about 60 ms (`cmd /c rem`), so in native Windows Claude Code both figures are about that much lower. The WSL profile's script (two `wslpath` calls and a file test on the Windows drive) measured 11 ms.
- Not run: macOS; a live Claude Code session showing the notice on a prompt.
