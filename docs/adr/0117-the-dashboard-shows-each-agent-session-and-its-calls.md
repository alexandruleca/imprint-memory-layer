---
title: ADR 0117 — The dashboard shows each agent session and its calls
description: Every MCP server process records its client, folder, workspace and tool calls in a per-installation SQLite file; the dashboard's Agents page lists them.
---

- Status: Accepted
- Date: 2026-10-29
- Related: [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (which clients Imprint connects to), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) (redaction), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (session workspaces)

## Context

The owner asked for a dashboard page that shows every agent connected to Imprint's MCP server and its run history.

Nothing recorded this:

- Each client starts its own stdio MCP server, `python -m imprint`. Many processes run at once, one per agent session, and each serves one client.
- The dashboard is a separate process. `imprint clients` reports which clients are *registered*, not which are running or what they called.

fastmcp 4.1.3 passes the client's name and version to middleware in two ways:

- **Protocol before 2026-07-28:** in the `initialize` handshake.
- **Protocol 2026-07-28:** the client may send only `server/discover`, with no `initialize`. Its details are then on the session (`client_params.client_info`) at each request. Checked on 2026-10-29: fastmcp's in-memory client on that protocol never called `on_initialize`.

## Decision

- **One session per process.** At start, `python -m imprint` records a session in `data/agent_activity.sqlite3`: PID, working directory and workspace. At a clean exit it records the end time.
- **Recording middleware.** `_ActivityLog` in `server.py`:
  - It names the client from `initialize` or, failing that, from the first tool call's session.
  - It records every tool call: tool, start, duration, success or the error, the size of the result, and the session's current workspace.
- **Arguments are kept short and redacted.** Each string argument is cut to 160 characters, the summary to 600, and the result passes through the deterministic redaction rules (`redaction.redact`) before it is written. Results themselves are not stored.
- **Per installation, not per workspace.** One session can switch workspaces, so the database belongs to the installation. Each call row keeps the workspace current at the time.
- **Recording never fails a tool call.** A failed write is skipped. Recording adds one SQLite insert and one update per call.
- **Old data expires.** Sessions not seen for 30 days are deleted, with their calls, whenever a new session starts.
- **Connected status.** A session is connected while it has no end time and its PID is alive (`processes.is_alive`). A process killed without a clean exit counts as ended at its last activity.
- **Dashboard.** `GET /api/agents` returns the sessions, connected ones first, and their totals by client. `GET /api/agents/{id}/calls` returns one session's calls, newest first, paged back with `before`. The Agents page shows both and refreshes every 3 seconds.

## Rejected alternatives

- **Derive sessions from the process list.** It shows what runs now, but nothing about history or tool calls, and it cannot name the client.
- **Log to a file per process.** The dashboard would have to parse and merge many files. SQLite in WAL mode lets many writers and one reader share one file, as `chat_store` and the queue already do.
- **Store whole arguments and results.** They can hold memory content and source text. The page needs only enough to recognise a call, and redaction can't guarantee full arguments hold no secret.
- **Per-workspace databases.** A session that calls `use_workspace` would be split across databases.

## Consequences

- Every tool call costs a few local SQLite writes, measured in milliseconds or less.
- A redacted, shortened copy of agents' queries stays on disk for 30 days. It sits beside the memories, which already hold far more.
- Sessions of MCP servers that run in WSL under another data directory appear only in that installation's dashboard.
- A client that sends no name appears as "Unknown client".

## Invalidation conditions

- If clients come to share one long-running MCP server, for example over HTTP, then a session must be keyed by the MCP session instead of the process.
- If the per-call writes show up in tool latency, then calls should be buffered and written in batches.
- If users ask to keep history longer or not at all, then retention becomes a setting.

## Validation

- `tests/test_agent_activity.py`:
  - Recording, ending, paging and grouping by client.
  - A dead process counting as disconnected.
  - Redaction and shortening of arguments, and expiry after 30 days.
  - The middleware through a real MCP client, naming the client and recording a success and a failure.
  - Both endpoints.
- `imprint/ui/tests/agents.spec.ts`: the page's clients, sessions, call history, empty state and sidebar link.
- Executed on bedrock, 2026-10-29: `python -m imprint` started over stdio by a fastmcp client recorded the client's name and version, the folder, the workspace and one `device_context` call, and the session was marked ended when the client disconnected.
