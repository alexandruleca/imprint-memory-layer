---
title: ADR 0212 — Agents talk through comments on Tasks
description: Messages between agents, clients and the user are comments on Tasks, addressed to a client, a device or the holder; they reach a running agent as a notice on any Imprint tool reply, at session start, and in Claude Code on every prompt through a new UserPromptSubmit hook that stays only while it adds less than 100 ms.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: The notice on tool replies, the inbox and the session-start context. The Claude Code UserPromptSubmit hook and the dashboard's notifications are not built yet.
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

## Rejected alternatives

- **A chat channel outside Tasks.** Weighed in the design discussion; messages without a Task lose their context and their record.
- **Delivery through hooks only.** Codex has no hook that runs during a session's turns, so it would see messages only at session start.

## Consequences

- A message waits until its recipient next calls Imprint or, in Claude Code, until the next prompt. It is mail, not chat.
- Codex sees new comments on Imprint tool replies and at session start, not on every prompt.
- Setup gains one Claude Code hook, which `imprint disable` and the cleanup paths must also remove.

## Invalidation conditions

- If Codex or another client offers a hook that runs on each prompt and can add context, use it there.
- If the UserPromptSubmit hook adds 100 ms or more to a prompt on the owner's machines, remove it.
- If MCP clients let a server put a message in front of a running agent, deliver comments that way.

## Validation

- `tests/test_tasks.py`: comments kept with their author and addresses, mentions, delivery by name, client, device, client on device and holder (the holder when read), no notice of one's own comments, the user's inbox, and reserved names.
- `tests/test_tasks_tool.py`: a comment told once as a notice to the addressed client and then read in the inbox; a Stalling holder told once. `tests/test_hooks.py`: the session-start context with the project's Board, unread comments and the session's name, within its size.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,522 tests, passed on 2026-11-27 with the Board and built-in skills together.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_builtin_skills`, `test_registry`, `test_registry_files`, `test_skills_tool`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 187 tests, passed.
- Not run: macOS.
