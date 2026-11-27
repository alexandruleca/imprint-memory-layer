---
title: ADR 0214 — The rules block shrinks to a stub once agents keep searching first
description: The Imprint block that setup writes into each client's global rules file becomes a few always-on lines pointing to the built-in imprint skill, kept only if the share of agent sessions whose first Imprint call is search does not fall by more than 10 points.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: The short block for Claude Code, Codex, Cursor, Copilot and OpenCode, natively and in WSL profiles; the measurement on each device from its own agent sessions.
- Related: [ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md) (built-in skills), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (agent sessions and their calls), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) (managed client configuration), [ADR 0201](0201-upkeep-brings-what-is-stored-up-to-date-in-the-idle-slot.md) (upkeep)

## Context

On 2026-11-27 the owner proposed making "the imprint functionality be a built in skill that gets injected with other skills so that way maybe we can have a simpler config prompt added to each global agent md".

- Setup writes one shared block, `ImprintBase` ([`internal/instructions/instructions.go`](../../internal/instructions/instructions.go)), into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, and the Cursor, Cline, OpenCode and Copilot rules, between managed markers. The MCP handshake's `instructions` carry similar guidance to every client that honours them ([`server.py`](../../imprint/server.py), `_MCP_INSTRUCTIONS`).
- A rules file is always in the agent's context. A skill is loaded only when the agent judges that its description fits the task.
- Every Imprint tool call is recorded per agent session with its time, on the device that served it, for 30 days ([`agent_activity.py`](../../imprint/agent_activity.py), `RETENTION_DAYS`).
- Of the connected clients, Claude Code, Codex, Cursor, Copilot and OpenCode load skills from the folders Imprint writes them into ([ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md)); Cline and the desktop apps do not.

## Decision

**A short block for clients that load skills.** Setup writes these lines into the managed block of Claude Code's `~/.claude/CLAUDE.md`, Codex's `~/.codex/AGENTS.md`, Cursor's `imprint.mdc` (after its front matter), Copilot's `copilot-instructions.md` and OpenCode's `imprint.md` ([`instructions.go`](../../internal/instructions/instructions.go), `ShortBase`):

```markdown
# Imprint Memory
Imprint is connected: it holds this project's past work, decisions and its Board of Tasks.
Search it first with Imprint's search tool, before reading files or guessing.
Load the `imprint` skill for how to use it, and `imprint-tasks` to track work.
```

The full guidance moves into the built-in `imprint` skill ([ADR 0213](0213-built-in-skills-ship-with-each-release-and-a-fork-replaces-them.md)). Cline and the desktop apps, which load no skills, keep the full block and the desktop snippet. The setting `instructions.full_rules`, a comma-separated list of clients, gives the listed ones the full block anyway. Setup records in `data/instructions.json` when each client first got the short block, once ([`rules_mode.go`](../../cmd/rules_mode.go)).

**Measured before it stays.** For each client, the share of its agent sessions whose first Imprint call is `search` is compared over the 14 days before and the 14 days after that switch ([`search_first.py`](../../imprint/search_first.py)). Only sessions that called Imprint count, each by its first call, from the sessions this device recorded. Upkeep decides once per client, as a step of its own for the device:

- With at least 10 sessions in each window and a drop of more than 10 percentage points, the full block returns: the client joins `instructions.full_rules`, a queued `connect` rewrites its rules, and the dashboard tells the user once.
- With a smaller drop, the short block stays.
- With fewer than 10 sessions after the switch, the window after grows until it has 10, for up to 6 weeks. With fewer than 10 before, or still too few after 6 weeks, the short block stays.

Upkeep counts the window before on its first pass after the switch and keeps the counts in `data/instructions.json`, since agent sessions are kept for 30 days. The Agents page and `imprint doctor` show each client's before, after and decision.

## Rejected alternatives

- **Replacing the block with the skill entirely.** A skill loads only when the agent judges it fits, so "search first" would be skipped in exactly the sessions that do not think to look.
- **Keeping the full block.** It is the same text in six places, refreshed only when setup runs again, and always in context whether or not the session uses Imprint.
- **Counting `recall` as searching first.** The decision named `search`; `recall` without a plan is a search, and a session that starts with it counts as not searching first. Weighed and left as decided, so the measurement errs toward restoring the full block.
- **Measuring across paired devices.** Agent sessions are recorded on the device that served them and are not synced; each device decides for its own clients from its own sessions.

## Consequences

- Every skill-loading client's always-on prompt is four lines, and the detailed guidance changes with each release through the skill.
- A client reaches the short block the next time setup or `imprint connect` runs for it, including through `imprint update`.
- A device whose dashboard does not run, or whose background upkeep is off, decides nothing until `imprint upkeep` runs; the window before is lost if no pass runs within 16 days of the switch, and the short block then stays for too few sessions.
- A restored client keeps the full block until the user takes it out of `instructions.full_rules`; the decision is not made again.

## Invalidation conditions

- If clients start loading named skills into every session, the stub can shrink further.
- If agent sessions sync between devices, measure across them.
- If restores turn out common, revisit the skill's description or the stub's wording before shortening other clients' rules.

## Validation

- Go `cmd/rules_mode_test.go`: each client's block (short for the five, full for Cline), `instructions.full_rules` from `config.json` and the environment taking precedence, a non-local data folder read and written nothing; the first switch recorded once per client with the rest of the file kept; disconnect removing Cursor's and OpenCode's rule files in the short form. `setup_copilot_test.go`, `setup_opencode_test.go` and the WSL profile tests write the short block.
- `tests/test_search_first.py` (11 tests): the share by each session's first call, only sessions that called Imprint, other clients and windows left out; restore after a drop of more than 10 points, once, with a fake connect; kept after a smaller drop; the window after growing until a client reaches 10 sessions, and too few after 6 weeks; too few before at 14 days; the window before kept after the Agents page forgot its sessions; upkeep running the step once per device without the vector store; the comparison, doctor's line and `/api/agents/search-first`. `tests/test_builtin_skills.py`: the `imprint` skill valid, holding the guidance, and every tool call in a built-in naming a tool and arguments the server has.
- Playwright `agents.spec.ts`: the Searching first panel and the restore toast told once. The panel was checked at 1280 px in light and 390 px in dark from screenshots.
- Linux (WSL Ubuntu, Python 3.12 venv): the Go suite and the full Python suite, 1,567 tests, passed on 2026-11-27.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `test_search_first`, `test_builtin_skills` and `test_upkeep`, and the cross-compiled `cmd` rules, cleanup, Copilot, OpenCode, WSL and hook tests, passed.
- Not run: a live 14-day comparison; macOS.
