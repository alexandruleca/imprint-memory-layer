---
title: ADR 0210 — Claims are leases held by the agent session
description: An agent takes a Task by claiming it; the claim belongs to the agent session and its Imprint server renews the lease while the session lives, a Task turns Stalling after 60 minutes without progress, and it turns Stale, its claim released, when renewals stop for 15 minutes or progress stops for 120.
---

- Status: Accepted
- Date: 2026-11-27
- Scope: On one device. The WSL bridge does not forward `CLAUDE_CODE_SESSION_ID` yet, so sessions served across it are held by their process.
- Related: [ADR 0208](0208-agents-track-work-as-tasks-on-a-board-in-each-workspace.md) (the Board), [ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md) (the log that orders claims), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (agent sessions), [ADR 0211](0211-agents-take-ready-and-stale-tasks-unasked-within-their-project.md) (who may take a Stale Task)

## Context

The owner asked that agents "detect stale issues or stalling issues etc and be able to pick them up to finish them" (2026-11-27). Telling a working agent from a gone one needs to know who holds a Task and whether that holder is alive.

- An Imprint MCP server records a random id per process, its pid, working directory and workspace, and the client's name and version from the MCP handshake; it records no client conversation id ([`agent_activity.py`](../../imprint/agent_activity.py), `start_session`, `name_client`). A restarted server is a new row with no link to the old one ([`__main__.py`](../../imprint/__main__.py)).
- Claude Code 2.1.332 passes `CLAUDE_CODE_SESSION_ID`, equal to its transcript's name, to the MCP servers it starts (observed on 2026-11-27). Under WSL that child is the bridge to the Windows server, and the bridge sets only `IMPRINT_*` variables ([`wsl_bridge.py`](../../imprint/wsl_bridge.py)). Whether Codex passes a conversation id was not determined.
- No client gets a hook after every tool call. Claude Code's hooks fire after Imprint's search and before Read, Grep and Glob (`cmd/setup.go`); Codex gets SessionStart and Stop only, because its tool hooks fire on Bash only (`cmd/setup_codex.go`).
- Paired devices sync about once a minute ([sync](../sync.md)), through a relay that forwards frames between them.

## Decision

**Who holds a claim.** A claim belongs to the agent session: the client's own conversation id where the client reports one (Claude Code's `CLAUDE_CODE_SESSION_ID`, which the WSL bridge will also forward), otherwise the Imprint server process. Claiming returns a claim token. The agent keeps it in its conversation, not on the Board, where checkpoints sync and anyone could read it, and a resumed conversation or a restarted server takes its claim back with it. The session is `conversation:<id>` for a client conversation, else `process:<the server's agent session>`.

**Renewal.** The holder's Imprint server renews the lease of each of its claims in the background while its process runs; the agent does nothing to keep a claim besides recording checkpoints. On the holder's own device, a holder whose process has ended releases its claims at once.

**Stalling and Stale.** A claimed Task is *Stalling* after 60 minutes without a checkpoint or other Imprint activity from its holder: the Board marks it and the holder is told once, in the notice on its next Imprint reply ([ADR 0212](0212-agents-talk-through-comments-on-tasks.md)). It is *Stale* when renewals stop for 15 minutes (`tasks.lease_minutes`: the process or its device is gone) or when there has been no progress for 120 minutes (`tasks.stale_minutes`). A Stale Task's claim is released, and the next agent takes it over from its handoff: the last checkpoint, the task branch and its last commit, the files touched, and a link to the previous holder's conversation as Imprint learned it.

**Concurrent claims.** Two agents on different devices can claim one Task within a sync interval. The earlier claim in the log's order ([ADR 0209](0209-a-tasks-history-is-a-log-of-events-that-devices-merge-by-union.md)) wins, and the other agent learns on its next Imprint call that it lost the claim and to whom. The built-in skill tells agents to check their claim again before long work.

**Task branches.** An agent works on a branch named `task/<id>-<slug>` and records the branch and its last commit in every checkpoint. Committing and pushing follow the repository's and the user's own rules; a claim never grants permission to push. A takeover on another device starts from the last pushed state.

## Rejected alternatives

- **The claim belongs to the Imprint server process.** A resumed conversation or a reconnected MCP server gets a new process, so it would look like a dead holder and lose its claims.
- **Renewing the lease from hooks after each tool call.** No client offers such a hook (see Context).
- **Renewing the lease only when the agent calls Imprint.** An agent that edits files for 40 minutes without an Imprint call would lose its claim while working.
- **A lock across devices.** It needs a live connection between the devices at the moment of claiming; pairs exchange data about once a minute through a relay.
- **An alive but idle session keeps its claims indefinitely.** Weighed in the design discussion; the owner chose that a session waiting for input for 120 minutes releases its claim.

## Consequences

- A session left waiting for input loses its claim after two hours, and its Task can be taken over.
- Claims across devices are optimistic: two agents can work on one Task for up to about a minute before one learns it lost.
- In a client that reports no conversation id, a restarted server keeps its claims only if the agent presents its claim token.
- Every Imprint server process runs a background renewal while it holds claims, and the WSL bridge forwards one more variable.

## Invalidation conditions

- If Codex or another client exposes its conversation id to MCP servers, use it as the holder for that client.
- If double claims across devices waste work in practice (lost-claim events on the owner's Boards more than once a week), add a claim check through the relay before a claim is confirmed.
- If Tasks are taken over from holders that were still working (the previous holder checkpoints after losing its claim), raise the defaults.

## Validation

- `tests/test_tasks.py`: claim and token, refusal of a live claim, token takeover, release at once when the holder's process has ended, lease expiry and renewal, Stalling and Stale by progress, renewals carrying activity, checkpoints by the holder only, release to Ready, the Review and Done rule, claims refused for the user's and ended Tasks, the earlier of two claims winning, and the Board reading claims with their leases.
- `tests/test_tasks_tool.py`: claim, checkpoint, token takeover and release through the tools; a lost claim told once; the server renewing its own session's leases.
- Linux (WSL Ubuntu, Python 3.12 venv): the full suite, 1,495 tests, passed on 2026-11-27.
- Windows 11 (the installed runtime's Python 3.13, from a native copy of the repository): `tests.test_tasks`, `test_tasks_tool`, `test_cli_tasks`, `test_hooks`, `test_agent_activity`, `test_config_help`, `test_chat_tool_coverage`, `test_adr_records` and `test_docs_tables`, 121 tests, passed.
