---
title: ADR 0214 — The rules block shrinks to a stub once agents keep searching first
description: The Imprint block that setup writes into each client's global rules file becomes a few always-on lines pointing to the built-in imprint skill, kept only if the share of agent sessions whose first Imprint call is search does not fall by more than 10 points.
---

- Status: Proposed
- Date: 2026-11-27
- Related: [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (built-in skills), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (agent sessions and their calls), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) (managed client configuration)

## Context

On 2026-11-27 the owner proposed making "the imprint functionality be a built in skill that gets injected with other skills so that way maybe we can have a simpler config prompt added to each global agent md".

- Setup writes one shared block, `ImprintBase` ([`internal/instructions/instructions.go`](../../internal/instructions/instructions.go)), into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, and the Cursor, Cline, OpenCode and Copilot rules, between managed markers. The MCP handshake's `instructions` carry similar guidance to every client that honours them ([`server.py`](../../imprint/server.py), `_MCP_INSTRUCTIONS`).
- A rules file is always in the agent's context. A skill is loaded only when the agent judges that its description fits the task.
- Every Imprint tool call is recorded per agent session with its time, for 30 days ([`agent_activity.py`](../../imprint/agent_activity.py), `RETENTION_DAYS`).

## Decision

**A stub.** The managed block shrinks to a few always-on lines: Imprint is connected; search it first; load the `imprint` skill for how to use it and `imprint-tasks` to track work. The full guidance moves into the built-in `imprint` skill ([ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md)). The short snippet for desktop apps, which do not load skills, stays as it is.

**Measured before it stays.** For each client, the share of agent sessions whose first Imprint call is `search` is compared over the 14 days before and the 14 days after the change. If it falls by more than 10 percentage points for a client, that client's full block is restored.

## Rejected alternatives

- **Replacing the block with the skill entirely.** A skill loads only when the agent judges it fits, so "search first" would be skipped in exactly the sessions that do not think to look.
- **Keeping the full block.** It is the same text in six places, refreshed only when setup runs again, and always in context whether or not the session uses Imprint.

## Consequences

- Every client's always-on prompt gets shorter, and the detailed guidance changes with each release through the skill.
- The measurement needs both 14-day windows within the 30 days agent sessions are kept.
- Clients that load no skills (Cline, desktop apps) keep the instructions they get today.

## Invalidation conditions

- If the measured share falls by more than 10 percentage points for a client, restore that client's full block.
- If clients start loading named skills into every session, the stub can shrink further.

## Validation

Proposed: nothing is implemented and nothing was executed.
