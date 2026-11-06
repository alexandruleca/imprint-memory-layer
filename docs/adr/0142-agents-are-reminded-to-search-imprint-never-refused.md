---
title: ADR 0142 — Agents are reminded to search Imprint, never refused
description: The hook that refused Read, Grep and Glob until a session had searched Imprint now reminds the agent once per session to prefer searching first, and always lets the tool run.
---

- Status: Accepted
- Date: 2026-11-06
- Related: [ADR 0003](0003-local-mcp-and-portable-hooks.md), [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md), [integrations](../integrations.md)

## Context

Since 2026-10-23, Imprint's setup installed a gate for Claude Code (`PreToolUse` on `Read|Grep|Glob`) and Cursor (`preToolUse` on `Read|Grep`). Until the session had called Imprint's search, the hook exited 2 and printed "Imprint: search prior context before Read/Grep/Glob.", which both clients treat as refusing the tool. A `postToolUse` hook on the search marked the session as having searched. The gate had no decision record; the README listed it as "Hard (PreToolUse)" enforcement.

On 2026-11-06 the user reported that Cursor's Bugbot "failed because of imprint". Agents that read files before searching are stopped by the gate:

- review agents such as Bugbot;
- scripted `cursor-agent` runs;
- a session whose Imprint server is broken or stale. The user's Cursor kept an Imprint server from 2026-11-02 running across updates, and it rejected calls.

The user chose: never refuse, only remind, and phrase the reminder as a preference to search Imprint first, for the least friction.

The clients' documentation (read 2026-11-06):

- **Claude Code:** a `PreToolUse` hook that exits 0 may return `hookSpecificOutput.additionalContext`, which Claude sees beside the tool result.
- **Cursor:** `preToolUse` is a permission hook. Its output fields are `permission`, `user_message`, `agent_message` (sent only on denial) and `updated_input`, and invalid output denies the tool, so it cannot carry a note. `postToolUse` may return `additional_context`, which the agent sees after the result.

Cursor's hook payload names the session `conversation_id`, not `session_id`. The hook read only `session_id`, so every Cursor session shared one "searched" marker.

## Decision

**The read hook never refuses a tool.** For a session that has not searched Imprint, the first read gets one reminder, and no read after it does:

> "Imprint may already hold context for this: earlier decisions, findings and conversations. Prefer searching it with Imprint's search tool before reading or grepping further; if Imprint is unavailable, carry on."

A session that searched first gets no reminder.

- **Claude Code** (including Claude Code inside WSL) keeps its `PreToolUse` hook, which now exits 0 and returns the reminder as `additionalContext`.
- **Cursor** gets the reminder from a new `postToolUse` hook on `Read|Grep`, as `additional_context`. Its `preToolUse` hook is removed at setup. Until setup runs, an installed `preToolUse` hook says nothing and allows the tool.

Sessions are keyed by `session_id`, or by Cursor's `conversation_id`, still hashed before use as a file name.

## Rejected alternatives

- **Refusing only the first read.** It keeps a failure on the first read, the one that stops review agents.
- **Keeping the hard gate for Claude Code only.** The same agents and failure modes exist there: subagents, scripts, an unavailable server. The user asked for the least friction.
- **Reminding on every read.** The note would fill the context of a session that has decided not to search.
- **Cursor's `agent_message` on `preToolUse`.** Cursor sends it only when the tool is denied.

## Consequences

- Automated agents and sessions without a working Imprint read files without failing.
- An agent may read before searching. The reminder, the rules and the session-start note make searching first the preferred path, but they cannot require it.
- Cursor's earlier hooks are replaced when setup or `imprint connect cursor` runs; the installed hook's command stops refusing as soon as Imprint updates.

## Invalidation conditions

- If agents routinely ignore the reminder and answer without Imprint's context, consider a stronger nudge that still never refuses, such as a reminder that repeats after some number of reads.
- If Cursor's `preToolUse` gains a non-denying field the agent sees, move Cursor's reminder before the read, as for Claude Code.

## Validation

- `tests/test_hooks.py`:
  - Claude Code's hook allows the read, returns the reminder once and nothing after it, says nothing to a session that searched first, and keeps session names inside the sessions folder;
  - Cursor's `preToolUse` says nothing, and its `postToolUse` reminder comes once per `conversation_id`.
- `internal/hooks/commands_test.go`: the hook commands run with spaces and apostrophes in paths and exit 0 before and after a search.
- `internal/jsonutil/preservation_test.go`: Imprint's Cursor entries for two matchers of one event coexist; the old `preToolUse` gate is removed and a user's own `preToolUse` handler kept.
- Not verified: the reminder as Cursor's agent and Bugbot see it.
