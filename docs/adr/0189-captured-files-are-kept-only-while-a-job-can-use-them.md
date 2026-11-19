---
title: ADR 0189 — Captured files are kept only while a job can use them
description: Delete a Chrome file capture's stored bytes once it is indexed, or a week after it failed, and zero them on disk.
---

- Status: Accepted
- Date: 2026-11-19
- Amends: [ADR 0187](0187-browser-file-capture.md) — uploaded files no longer persist in the queue for retries indefinitely
- Related: [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md), [ADR 0068](0068-merge-identical-waiting-jobs.md)

## Context

ADR 0187 stores each file the Chrome extension uploads, up to 25 MiB, in the
`browser_files` table of `data/queue.sqlite3`, keyed by its SHA-256, so a failed
capture can be retried without uploading it again. Nothing deleted those rows.
The queue keeps foreground job history without a limit (`_prune_background`
removes only background tagging runs), so every captured PDF, attachment and
screenshot stayed on disk after its text was indexed, in a database that is
owner-only but not encrypted. ADR 0187 named the gap as an invalidation
condition without closing it.

## Decision

A stored file is kept while a job that names it can still use it: a capture that
waits or runs, or one that failed or was cancelled less than seven days ago.
A capture that finished has been indexed and keeps nothing. Files no job names
are deleted too.

`queue.collect_browser_files()` applies that rule in one `DELETE` after any Chrome
capture job ends, off the event loop, and when the dashboard API starts. It
deletes with SQLite's `secure_delete` on, so the freed pages are overwritten
with zeros, and then truncates the write-ahead log, which still holds the
inserted pages.

Retrying a capture whose file is gone, or running a finished one again, is
refused with a message to capture it again from Chrome. A retry queues a job
that names the file, so the file outlives the failed job's window for as long
as the retry waits or runs.

## Rejected alternatives

- **Keep files until the user deletes them.** This is ADR 0187's behaviour.
  Job history is never pruned and no page lists stored files, so in practice
  nothing would be deleted.
- **Delete the file when its job ends, whatever the outcome.** This breaks
  Retry for a failed capture, which ADR 0187 promises and
  `test_binary_upload_is_bounded_and_can_be_retried_without_reuploading`
  covers. The file may also be one Chrome can no longer reach, for example
  once a provider session ends.
- **A plain `DELETE`.** With `secure_delete` and the log truncation removed,
  the retention test found the deleted file's bytes still in `queue.sqlite3`
  and its WAL.

## Consequences

- Indexed captures no longer grow the queue database. Its file does not shrink:
  later writes reuse the zeroed pages.
- **Run again** on a finished file capture, and **Retry** after the window, are
  refused instead of re-indexing from the stored bytes. The indexed text is
  unaffected.
- Collection runs only when a Chrome capture ends or the dashboard API starts.
  An expired file therefore stays until one of those happens.
- If a reader holds the log at that moment, the truncation does nothing. The
  old frames remain until a later truncation or until they are overwritten.
- Zeroing happens inside SQLite's files. It does not guarantee that the bytes
  are physically erased from SSDs, copy-on-write file systems, snapshots or
  backups.
- A text capture's content still stays in its job body in history, as before.

## Invalidation conditions

- If users report retries refused because the week had passed, lengthen the
  window or make it a setting.
- If files move out of SQLite into a spool, as ADR 0187 foresees for larger
  uploads, delete the spool's files by the same rule and drop the SQLite-specific
  zeroing.
- If job history gains a retention limit, decide whether text captures' job
  bodies should follow the same rule.

## Validation

`tests/test_browser_capture.py` adds two tests, run on Linux with Python 3.12:

- **`FileRetentionTests`**, covering:
  - done, recently failed, expired, waiting, shared-digest and orphaned files;
  - refusal of retries for files that are gone;
  - a retry keeping its file after the failed job expires;
  - the deleted bytes no longer being in `queue.sqlite3` or its WAL. With the two pragmas removed, this check fails.
- **A dispatcher test** that runs a failing capture through `_run_job` in a real subprocess, then `recover_on_startup()`, and checks both collect.

The full Python suite passed on Linux. Windows and macOS were not run locally.
