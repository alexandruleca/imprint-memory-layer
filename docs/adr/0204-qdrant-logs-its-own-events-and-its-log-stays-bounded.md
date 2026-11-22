---
title: ADR 0204 — Qdrant logs its own events, and its log stays bounded
description: The managed Qdrant daemon logs its own events at info but not each HTTP request, and qdrant.log is cut back to its last megabyte once it passes 10 MB, at start everywhere and while running on Linux and macOS.
---

- Status: Accepted
- Date: 2026-11-22
- Related: [ADR 0063](0063-auto-spawned-local-qdrant-server.md) (the auto-spawned daemon), [ADR 0205](0205-upkeep-backs-off-when-the-store-is-slow.md) (found in the same investigation), [architecture](../architecture.md)

## Context

- **The size.** On 2026-11-22 `data/qdrant.log` on the owner's Linux device `bedrock` was 1.26 GB after three days of uptime. Almost every line was `INFO actix_web::middleware::logger`, one per HTTP request. Background tagging, upkeep and paired-device sync send thousands of requests an hour. The owner asked for the log to be cleaned up regularly, so its history stops taking space.
- **The setting that did nothing.** `qdrant_runner._spawn` set `RUST_LOG=warn,qdrant=info`. A test of the pinned Qdrant 1.17.1 on 2026-11-22 showed it is ignored: two HTTP requests still logged two actix lines. Qdrant reads its filter from `QDRANT__LOG_LEVEL`, and the same test gave these results:

  | Setting | Lines | Request lines | Qdrant's own info lines |
  |---|---|---|---|
  | `RUST_LOG=warn,qdrant=info` (before) | 27 | 2 | 13 |
  | `QDRANT__LOG_LEVEL=WARN` | 16 | 0 | 0 |
  | `QDRANT__LOG_LEVEL=info,actix_web=warn` | 21 | 0 | 5 |

- **How the daemon writes.** The daemon writes its output into `qdrant.log` through a descriptor opened for appending (`"ab"`). It runs for days, and nothing rotated the file.

## Decision

- **Level.** The daemon starts with `QDRANT__LOG_LEVEL=info,actix_web=warn`, unless the environment already sets `QDRANT__LOG_LEVEL`. Qdrant's own events (start, recovery, warnings) stay; per-request lines go. The ignored `RUST_LOG` default is removed.
- **Bound.** Once `qdrant.log` passes 10 MB, its last 1 MB, from the first whole line, becomes `qdrant.log.1` (replacing the previous one), and the log starts again empty.
  - This happens before each start, on every platform.
  - On Linux and macOS it also happens whenever an Imprint process connects to a running managed daemon (`ensure_running`). The daemon appends, so its next line lands at the new end. Queue jobs and sessions connect many times a day, so a running daemon's log is checked far more often than daily.
  - On Windows, a running daemon's inherited handle keeps its own position, and emptying the file would leave a run of zeros. Its log waits for the next start.
  - A server Imprint does not manage (`IMPRINT_QDRANT_NO_SPAWN`) is left alone.

## Rejected alternatives

- **`QDRANT__LOG_LEVEL=WARN`.** It is smallest, but it drops the startup and recovery lines that `imprint server log` exists to show.
- **A daily scheduled job.** Imprint has no scheduler that runs without the dashboard. Checking on connect costs one `stat` and reaches every device that runs anything.
- **Renaming the log while the daemon runs.** The daemon keeps writing to the renamed file, so it would still grow. On Windows the rename fails while the file is open.
- **Spawning with an append-only handle on Windows,** so that a running daemon's log could be emptied there too. It needs `CreateFileW` through `ctypes`. With per-request lines gone, a Windows log grows by a few lines a day between starts, so it was not worth the platform code.

## Consequences

- `qdrant.log` holds at most about 10 MB, plus 1 MB in `qdrant.log.1`, on every device.
- A daemon started before this change keeps logging every request until it restarts. On Linux and macOS its log is still cut back on each connection; on Windows it shrinks at the next start.
- Request-level history is no longer available. Setting `QDRANT__LOG_LEVEL=info` before Imprint starts the daemon brings it back for debugging.

## Invalidation conditions

- If Qdrant honours `RUST_LOG`, or changes how `QDRANT__LOG_LEVEL` filters, revisit the setting.
- If a Windows daemon's log grows past the limit between starts in practice, spawn it with an append-only handle and trim it while it runs there too.
- If Qdrant gains log rotation of its own, use that instead.

## Validation

- Linux x64 (WSL2): the pinned Qdrant 1.17.1 binary, downloaded through the runner, was run with each setting in the table and sent two HTTP requests.
- `tests/test_qdrant_security.py` checks the level in the spawn environment. It also checks a trim that keeps the last lines from a whole line, a log under the limit left alone, a running daemon appending at the new end, a Windows daemon's log waiting for the next start, and trimming on start and on connect but not for an unmanaged server. It passed on Linux (Python 3.14) and on Windows (Python 3.13), where the append test skips itself.
- Not run: the change against bedrock's live daemon. It applies after `imprint update` and a Qdrant restart there.
