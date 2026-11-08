---
title: ADR 0158 — Reminders, timers and scheduled jobs fire from the API process
description: Reminders, timers, scheduled queue jobs and scheduled questions live in a device-local SQLite store and fire from a scheduler thread in the dashboard API, once per occurrence across restarts; chat and voice set them through personal tools, and the dashboard shows each firing as a toast and desktop notification until it is done or snoozed.
---

- Status: Amended
- Date: 2026-11-08
- Scope: Linux (WSL2) executed; the scheduler, the dashboard page and the desktop notification on Windows and macOS are unvalidated (see Validation)
- Amended by: [ADR 0161](0161-chat-keeps-its-instructions-a-reusable-prompt-prefix.md) — the current time follows the user's question in a chat request instead of sitting in the instructions
- Related: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0068](0068-merge-identical-waiting-jobs.md), [ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md), [ADR 0015](0015-workspace-roots-and-source-purge.md), [ADR 0152](0152-dashboard-summaries-are-served-stale-while-they-refresh.md), [dashboard](../dashboard.md), [API](../api.md), [queue](../queue.md)

## Context

The user asked to tell Imprint, by chat or by voice, things like "set a timer for 10 minutes", "remind me at 10 am to call Dan", "every weekday at 9 re-index my code folder" and "tomorrow at 8 summarize what I worked on yesterday". Before this record nothing in Imprint ran at a time: the queue ([ADR 0064](0064-single-slot-command-queue-and-shared-lock.md)) starts jobs only when someone submits them, and the chat agent could not know the date or time, so "tomorrow" or "at 10" had nothing to resolve against.

Constraints found in the code:

- **Voice is read-only.** Echo runs `chat.stream_reply(..., read_only=True)` (`imprint/voice_api.py`), and `_run_tool` refused every tool with `writes=True`; the same flag also hid those tools from the schemas. Setting a reminder writes, so voice could not set one.
- **One long-lived process.** The dashboard API (`imprint/api.py`) already runs the queue dispatcher and the sync worker from its startup event and owns chat replies that run to completion ([ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md)). The MCP server is one process per client session and comes and goes with it.
- **Live events.** The dashboard learns about jobs by polling `GET /api/queue` every 2 s through one shared store (`imprint/ui/src/lib/queue.ts`) and announces them with sonner toasts (`job-toasts.tsx`); `/api/stream` only reports that data changed.
- **The desktop app** keeps the dashboard window alive while hidden (recordings already run there, [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md)), so its page can raise alerts while the user works elsewhere.

## Decision

Reminders, timers and scheduled jobs are entries in `data/schedule.sqlite3` (`imprint/schedule.py`), device-local, never synced and outside every workspace. An entry is a `reminder` (a message), a `timer` (a duration that counts down, at most 7 days, never repeating), a `job` (one queue command from an allow-list) or a `prompt` (a question for Imprint). Each keeps its kind, title, message, next due time (UTC), the IANA time zone it was made in, its recurrence (none, daily, weekdays, weekly on given days, or every N minutes or hours, at least 5 minutes), the workspace active when it was made, its payload, its status (`scheduled`, `fired`, `done`, `cancelled`, `missed`), who made it (`chat`, `voice`, `dashboard`), when it last fired and any snooze.

**Times.** A due time is a local ISO-8601 date-time (one with an offset is honoured), a time of day (the next one), or a duration from now. A date-time already past is refused with the current time and asks the model to ask the user. A local time the clocks skip moves on by the gap and says so; a repeated one is its first occurrence. Daily, weekday and weekly recurrences keep their local wall-clock time across daylight-saving changes; an interval counts from the previous occurrence.

**Firing.** A scheduler thread started by the API's startup event sleeps until the next due entry or snooze (at most 60 s, so a clock jump is noticed), and any change wakes it. Firing an occurrence inserts a firing row (`UNIQUE(entry_id, occurrence_at)`) and moves the entry on with a compare-and-set on its due time, in one transaction, before anything acts on it: an occurrence fires at most once, across restarts and even with two processes. An occurrence missed while the server was down fires once on the next start, marked late; a recurring entry then moves to its next future occurrence, never one firing per missed slot. A job or question missed by more than 12 hours is not run late; its firing says it was missed.

**What a firing does.** A `job` is submitted through `queue.submit` in the entry's workspace, so it merges with an identical waiting job ([ADR 0068](0068-merge-identical-waiting-jobs.md)) and an ingest or refresh outside the workspace's approved roots is refused ([ADR 0015](0015-workspace-roots-and-source-purge.md)); the allow-list is `ingest`, `refresh` (both need an absolute folder, checked against the roots when the entry is made), `learn`, `organize`, `refresh-urls` and `retag`, each with its own typed fields and never passthrough arguments or a shell. A `prompt` starts a normal chat reply ([ADR 0145](0145-a-request-runs-until-it-is-answered-or-explicitly-stopped.md)) in a new chat of the workspace active when it fires. A reminder or timer only alerts.

**Delivery.** Every firing is an alert that waits for the user. The dashboard polls `GET /api/schedule/alerts` every 3 s through one shared store, as it polls the queue, and shows each as a toast on any page: a reminder or timer stays until **Done** or **Snooze** 5, 10 or 30 minutes, and a snoozed alert rings again; a job or question is reported once, with **Open queue** or **Open chat**. In the desktop app the dashboard also asks the main process, through a validated `reminder-notify` message, for a native notification when the dashboard is not in front; clicking it shows the dashboard on the reminder. Echo does not announce reminders.

**Chat and voice.** The chat tools `set_reminder`, `set_timer`, `schedule_job`, `schedule_prompt`, `list_reminders` and `cancel_reminder` are marked `personal` on the `Tool` registry: they write only this device's schedule, so read-only voice offers and runs them while `store`, `kg_add`, `kg_invalidate` and `ingest_url` stay refused. Each result restates the resolved time in the user's local time ("Reminder set for tomorrow, Mon 9 Nov 10:00"). The chat instructions carry the current local date, time, weekday and zone.

**Dashboard.** A Reminders page lists what rang, upcoming entries by day with live countdowns for timers, and past entries by calendar period, both paged by cursor; it creates, edits and cancels entries. `/api/schedule` is the API: list (`view=upcoming|past`, `before` cursor), create, read, update, snooze, done and cancel.

## Rejected alternatives

- **An OS scheduler (cron, Task Scheduler, launchd).** It would fire with the dashboard closed, but needs per-platform installation code, client configuration outside Imprint's data folder, and its own path to the queue and to chat. The API process already owns the queue dispatcher and chat replies, and "fires once on the next start, marked late" covers the dashboard being down.
- **Firing from the MCP server.** It is one process per client session and may not run at all; several would race for the same entry.
- **A new live channel (WebSocket, or alert events on `/api/stream`).** `/api/stream` is subscribed only while a page asks for data-change revalidation, and the queue's toasts already poll. A 3 s poll of a small endpoint was enough for an alarm, and kept one pattern.
- **Running every missed job and question late.** "Every weekday at 9 re-index" missed for three days would queue a re-index on start, which is harmless once, but a question like "summarize yesterday" answered days later is wrong. Missed occurrences fire once for reminders and timers, and for jobs and questions only within 12 hours.
- **Exactly-once by acting first and recording after.** A crash between the two would fire again on restart. Recording first gives at most once; the only loss is an action interrupted mid-way, which the firing's empty outcome shows.
- **Letting voice run every write tool.** Store and the knowledge graph change shared memory and sync to other devices; a misheard sentence should not. Scheduling tools touch only this device's schedule, and every entry is visible and cancellable on the Reminders page.
- **The current time only in the user's message.** It would keep a local model's evaluated prompt across replies (the system prompt now changes every minute), but changed the message the conversation's tests and the research notes read; the time in the instructions, as requested, was kept.

## Consequences

- Reminders and scheduled jobs fire only while the dashboard API runs; the desktop app keeps it running in the background. With Imprint stopped, they fire late on the next start, as above.
- The chat instructions change every minute, so a local model re-evaluates the conversation at the start of each reply instead of reusing the previous reply's evaluated prompt. Within one reply they are constant, so ADR 0157's prefix charging is unaffected.
- Entries never sync: a reminder set on the laptop does not ring on the desktop.
- A scheduled question runs in the workspace active when it fires, not the one it was set in, because the chat session store records in the active workspace.
- A time zone the device cannot name falls back to the current fixed offset, and recurrences then do not follow daylight-saving changes. The dashboard sends the browser's zone with entries it makes.
- A desktop notification depends on the dashboard window's page running; one closed outside the app (a crash) shows nothing until it reloads, and the pending alerts then show as toasts.

## Invalidation conditions

- If users need reminders while Imprint is not running, move firing to an OS scheduler and keep this store as its source.
- If entries need to follow the user across paired devices, sync them as their own dataset with one device owning the firing, superseding the device-local decision.
- If the 3 s alert poll becomes a measurable load, or the dashboard gets a general event channel, deliver alerts on it instead.
- If a misheard voice request is shown to schedule an unwanted job, require confirmation for `schedule_job` in voice or remove `personal` from it.
- If reusing a local model's evaluated prompt across replies matters (measured as reply latency), move the clock out of the instructions.

## Validation

Executed on Linux (WSL2, Python 3.14, Node 22):

- `tests/test_schedule.py` (34 tests): absolute, offset and time-of-day resolution, a passed time refused with a question, durations, a skipped and a repeated local time across daylight-saving changes, weekday, weekly and every-N recurrences and their next occurrence, a daily time kept across a clock change, a reminder firing once and waiting, exactly once across a simulated restart (a recorded firing that never acted is not fired again), a missed recurring entry firing once late and moving to the next future occurrence, a stale job not run, a job submitted in its workspace, a question run as a chat reply, a failed action recorded, snooze ringing again, cursor paging, the API's validation (the job allow-list, unknown fields, passed times, bad zones, timers that repeat), the personal tools offered and run in read-only voice while `store` is refused, the resolved local time in results, and the current time in the instructions. Every clock is injected.
- The existing chat tests (`test_chat_stream`, `test_chat_analysis`, `test_chat_research`) pass with the instructions change.
- `imprint/ui/tests/reminders.spec.ts` (5 Playwright tests against the static export with a mocked API): grouping, a live countdown and cursor paging; creating a weekly reminder with the viewer's zone and cancelling it; a refused job keeping the form open with the reason; a toast on another page with snooze choices and Done, ringing again after a snooze; a scheduled question opening its chat.
- `ui-electron/main.test.js`: a reminder notifies only while the dashboard is not in front, malformed alerts and other origins are ignored, and a click shows the dashboard and sends `reminder-open`.

Not executed: a real scheduler thread over hours (the loop's sleep is covered only through `Scheduler.step`), native notifications on Windows, macOS and Linux desktops, Windows' time zone name lookup (`tzutil`), and a scheduled question answered by a real model.
