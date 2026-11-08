---
title: ADR 0151 — Agent sessions page by last activity with a cursor
description: GET /api/agents returns the connected sessions apart and the finished ones a page at a time, newest activity first, resumed from a (last_seen, id) cursor; client totals cover every session, and the Agents page groups the history by calendar period.
---

- Status: Accepted
- Date: 2026-11-08
- Amends:
  - [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) — `GET /api/agents` pages its sessions with a cursor and lists connected ones apart, instead of returning up to 100 sessions with connected ones first
- Related: [ADR 0139](0139-projects-and-sources-browse-in-paged-tabs.md) (paged lists elsewhere in the dashboard), [dashboard](../dashboard.md), [API](../api.md)

## Context

The user asked for the Agents page to split sessions "by this week, last week, last month etc. with pagination".

Before this record, `GET /api/agents` returned at most `limit` sessions (100 by default, 500 at most), connected first, and the page polled it every 3 seconds:

- **History was cut off silently.** Each client starts one session per process, and sessions are kept 30 days. With more than 100 the page showed "Sessions 100" and nothing older.
- **Client totals were wrong past the limit.** `clients` was computed from the returned sessions only, so a client's sessions, calls and errors counted only the newest 100.
- **Order moved under polling.** Connected sessions sat first and moved down when they ended; their `last_seen` changes with every call.

## Decision

- **Connected sessions apart.** `running` lists every connected session, on the first page only. A session is connected as ADR 0117 defines it: no end time and its process alive. The check runs only on rows without an end time.
- **The rest by last activity, with a cursor.** `sessions` lists the other sessions ordered by `(last_seen DESC, id DESC)`, `limit` at a time (default 50, at most 500). `next` is `<last_seen>|<id>` of the page's last session, or `null` on the last page. Passing it back as `before` returns the sessions strictly after it in that order. A finished session's `last_seen` no longer changes, so a cursor stays exact while new sessions arrive. A malformed cursor is refused with 400.
- **Totals over everything.** `total` counts the finished sessions that match. `clients` totals every recorded session by client in one SQL aggregate, whatever page is shown.
- **Filters on the server.** `client` (the name `clients` gives, `Unknown client` for none) and `q` (a case-insensitive substring of the folder, workspace or client, with `%` and `_` taken literally) narrow both lists and `total`.
- **The page merges polls into loaded pages.** It polls the first page. When older pages are loaded, the fresh first page replaces only the sessions down to its last one, and the loaded sessions after it stay. A session that ends or starts joins the top, and the list never shrinks under the reader.
- **Time groups are the page's.** The page groups finished sessions by `last_seen` in the viewer's time zone and locale week: Today, Yesterday, This week, Last week, This month, Last month, then one group per earlier month (`imprint/ui/src/lib/time-groups.ts`). The API stays in epoch seconds.

## Rejected alternatives

- **Offset paging (`offset=50`).** A session that starts or ends between two requests shifts every offset by one, so the next page repeats or skips a session. The page polls every 3 seconds and sessions end at any time.
- **Grouping in the API.** Calendar periods depend on the viewer's time zone and the first day of their week, which the browser knows and the service does not. Paging by group would also make a page's size depend on how busy a week was.
- **Raising the limit.** With hundreds of sessions, every 3-second poll would recount the calls of every session (the per-session subqueries of ADR 0117), and the list would still end silently.
- **Cursor on `started_at`.** It never changes, even for running sessions, but the history is read by when a session was last active, and a long-running session would sit among the day it started.

## Consequences

- The first response holds at most `limit` finished sessions plus the connected ones, so each poll stays bounded however long the history is.
- `sessions` no longer includes connected sessions. The Agents page is the only consumer and changed with it.
- `clients` runs one grouped count over `agent_calls` per poll. It reads the whole 30-day call table, as the per-session counts did for 100 sessions.
- A process that died without ending has `ended_at` empty in the database. It is counted in the history, at its last activity, by the same liveness check.

## Invalidation conditions

- If a session's `last_seen` can change after it ends, for example if a sync imports sessions from another device, then the cursor can skip or repeat it and must key on a value that never changes.
- If the `clients` aggregate shows up in poll latency, then the totals should be kept in a table that each call updates.
- If clients come to share one long-running MCP server (ADR 0117's own condition), then `running` must be keyed by the MCP session.

## Validation

- `tests/test_agent_activity.py` (`PagingTests`, `ApiTests`): pages cover the history once in order with running sessions only on the first page, a new session does not shift the next page, equal `last_seen` pages by id, client and text filters with literal `%` and `_`, malformed cursors are refused, and client totals count every session. Run on Linux (WSL2).
- `imprint/ui/tests/agents.spec.ts`: with the clock fixed on Wednesday 18 November 2026 in Europe/Bucharest (en-GB), sessions fall under Today (00:30), Yesterday (23:50), This week, Last week (two), This month, Last month (31 October 23:59), September and December 2025. Also tested: **Load older sessions**, loading on scroll, a poll that adds a session on top and keeps loaded pages, client and text filters, and retry after a failed load. Run in Chromium against the static export.
- Not verified: other browsers, and a real installation with hundreds of sessions. The tests use mocked API responses.
