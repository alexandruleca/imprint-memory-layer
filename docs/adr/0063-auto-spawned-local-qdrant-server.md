---
title: ADR 0063 — Run Qdrant as an auto-spawned local server
description: Every Imprint process reaches one Qdrant server on loopback that the first process to need it starts, because embedded mode admits one process at a time.
---

- Status: Accepted
- Date: 2026-04-13
- Recorded: 2026-10-25
- Retrospective: reconstructed from code, docs and git history: `imprint/qdrant_runner.py`, the inline rationale this record replaces in `docs/architecture.md`, and commit b4d504d, which introduced Qdrant; and the owner's answers on 2026-10-25
- Related: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (verified binary download, serialized first start), [ADR 0022](0022-http-client-lifetime-during-model-work.md) (removed the idle closer left from embedded mode), [ADR 0024](0024-enforce-local-service-boundaries.md) (loopback-only managed server), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) (heavy jobs are serialized separately), [ADR 0065](0065-qdrant-over-lancedb-as-the-vector-store.md) (why Qdrant rather than LanceDB)

## Context

Several Imprint processes use the memory store on one machine at the same time:

- the MCP server, which a client such as Claude Code keeps running for its whole session;
- hooks, which write extracted decisions and summaries at the end of a session;
- CLI commands such as `imprint ingest`, started from a terminal;
- the dashboard, which reads the collection while the others run.

Qdrant's embedded mode (`QdrantClient(path=...)`) runs the store inside the client process and locks its storage directory, so a second process cannot open it. `docs/architecture.md` stated the conflict: embedded mode "pins a filesystem lock and rejects any second client — this conflicts with Claude Code (always-on MCP) running alongside `imprint ingest`, hooks writing decisions, and tools like `imprint ui` reading the collection."

Commit b4d504d (authored 2026-04-13, "replaced db with qdrant") replaced the earlier LanceDB store with Qdrant; it adds `migrate_lance_import.py` to re-ingest a LanceDB dump. The same commit shows that embedded mode came first:

- The new vector store's docstring still read "Embedded mode: `QdrantClient(path=...)` uses on-disk local storage, no server."
- `server.py` started an idle watcher with the comment "Release the embedded Qdrant lock after 30s of no MCP activity so `knowledge ingest` (a separate process) can grab it. Without this, the MCP server pins the lock for the lifetime of Claude Code."
- The client it shipped already connected over HTTP to a server started by the new `qdrant_runner.py`, whose docstring says it "Replaces embedded mode (single-writer, lock collisions) with a long-running local server on 127.0.0.1:6333."

Whether embedded mode or the idle lock release was ever used is unknown: both appear only as leftovers in b4d504d, which already shipped the server, and the owner does not remember (asked on 2026-10-25).

The repository's earliest history was rewritten on 2026-04-14: commit dates differ from author dates, and the first commit's README already describes later features. This record therefore takes its date from b4d504d's author date, and its evidence of how the decision came about from that commit's code and the owner's answers. Why Qdrant replaced LanceDB is recorded in [ADR 0065](0065-qdrant-over-lancedb-as-the-vector-store.md).

## Decision

Qdrant runs as a separate server process on the local machine, and every Imprint process connects to it over HTTP, by default at `127.0.0.1:6333`.

- **Started on demand.** A process that needs the store calls `qdrant_runner.ensure_running()`. When a server already answers `/readyz`, it is used. Otherwise the runner takes a binary from `IMPRINT_QDRANT_BIN`, `data/qdrant-bin/` or `PATH`, or downloads the pinned release (`IMPRINT_QDRANT_VERSION`), starts it detached so it outlives the process that started it, writes `data/qdrant.pid`, logs to `data/qdrant.log`, and waits up to 30 seconds for `/readyz`.
- **Shared and long-lived.** The server keeps running after its starter exits. `imprint server stop`, `imprint disable` and uninstall stop it. Its data is in `data/qdrant_storage/` and `data/qdrant_snapshots/`.
- **Bring your own server.** `qdrant.no_spawn` (`IMPRINT_QDRANT_NO_SPAWN=1`) skips starting a server and connects to the configured host and port. The operator secures that server.

Later records constrain how the managed server runs without changing this choice. ADR 0007 verifies the downloaded binary against a checked-in SHA-256 manifest and serializes the first start with a shared lock and a health recheck. ADR 0024 refuses a non-loopback host for the managed server and disables cluster mode and browser CORS. ADR 0022 removed the idle closer left over from embedded mode.

## Rejected alternatives

- **Embedded mode.** The store would live inside one process, and its storage lock would shut out every other process while that one ran. An MCP server lives as long as its client session, so ingestion, hooks and the dashboard would be locked out for that long (b4d504d's docstrings; `docs/architecture.md`).
- **Embedded mode, releasing the lock while the MCP server is idle.** b4d504d's `release_idle_client` closed the client after 30 seconds without MCP activity (`KNOWLEDGE_MCP_IDLE_S`) so that a separate ingest could take the lock. Whichever process came second while the lock was held still could not open the store. The server made the workaround unnecessary. Its timer stayed until ADR 0022 removed it, after it closed an HTTP client during slow model work.

No other alternative evaluation was recorded.

## Consequences

- Any number of Imprint processes read and write at once; the server serializes writes internally.
- The first start downloads a binary unless one is provided, and a daemon stays resident until it is stopped. `docs/architecture.md` put the cost at about 50 MB of binary and about 50 MB of resident memory, figures the owner says came from an early benchmark that was never repeated. Measured on 2026-10-25 (see Validation), the pinned v1.17.1 release is a 28 to 35 MB download that unpacks to 96 MB on Linux x64 and 83 MB on Windows x64. The daemon's resident memory grows with the collection and with the number of CPUs it may use: on Linux x64 with 8 CPUs, 76 MB with no collections, 265 to 300 MB with 10,000 Imprint-like points and 579 to 673 MB with 100,000; with 32 CPUs, 179 MB, 521 to 607 MB and 792 to 894 MB. Half or more of each figure with points is file-backed pages of the binary and the memory-mapped storage, which the kernel can reclaim under memory pressure. These costs are several times the earlier figures. They do not by themselves reopen the decision: embedded mode, the rejected alternative, admits one process at a time whatever its memory use.
- Imprint binds local ports (HTTP 6333 and gRPC 6334 by default). A server that already answers on the configured port is used as it is; ADR 0024 records that such a listener cannot be assumed safe.
- Processes can race to start the server. ADR 0007's start lock serializes this.
- Every process depends on the daemon: if it cannot start, for example because its port is taken or no binary exists for the platform, storage fails and points at `data/qdrant.log`.
- Embedded-mode assumptions outlived the switch, such as the idle closer that ADR 0022 removed.

## Invalidation conditions

- If Qdrant's embedded client supports several processes reading and writing one storage path at once, retire the auto-spawned daemon in favor of direct disk access.
- If every read and write comes to run inside one long-lived process that owns the store, for example because MCP servers, hooks and CLI commands all reach it through the dashboard's API, the multi-process requirement is gone and embedded mode should be reconsidered.
- If Qdrant stops publishing a release binary for a supported platform, or one the checked-in manifest can verify, auto-spawn cannot serve that platform: ship a binary for it or require a separately managed server there.
- If the daemon's resident memory, measured with `scripts/measure-qdrant-memory.py` as under Validation, doubles at the same collection size and CPU count, for example after a Qdrant upgrade, re-weigh the daemon against storage that several processes open directly, such as LanceDB's open-source edition ([ADR 0065](0065-qdrant-over-lancedb-as-the-vector-store.md) compares the two).

## Validation

No validation was recorded with the original decision; the embedded-mode lock conflict is known from b4d504d's comments and `docs/architecture.md`, not from a recorded test. Checks that exercise the decision today:

- `tests/test_workspace_diagnostics.py`: two concurrent first starts spawn one server, concurrent first use never reads a collection while it is being created, and `imprint doctor` reports storage without starting a server.
- `tests/test_qdrant_security.py`: a non-loopback managed host fails before any probe, download or spawn; external connections need bring-your-own mode; inherited settings cannot enable cluster mode or CORS.
- `tests/test_downloads.py`: a corrupted download and an unmanifested version are refused.
- ADR 0007 records a native run of verified download, start, write and read, and ADR 0024 native Linux runtime and socket checks of the loopback binding.

Resident memory and disk use were measured on 2026-10-25 with `scripts/measure-qdrant-memory.py`:

- **Setup.** The pinned v1.17.1 Linux x64 binary: the release archive matches the manifest's SHA-256, and the binary in it is byte-identical to the cached copy that ran. Linux x64 under WSL2 (kernel 6.18, 32 logical CPUs, about 16 GB of memory). The script starts the server through `qdrant_runner` in a throwaway data directory on free ports and creates the collection through `vectorstore._ensure_collection`, so the server environment, collection settings and 13 payload indexes are Imprint's. It ran once with all 32 CPUs and once limited to 8 with `taskset -c 0-7`: Qdrant sizes its thread pools and default segment count by the CPUs available ([configuration reference](https://qdrant.tech/documentation/ops-configuration/configuration/)), and the collection had 8 segments with 32 CPUs and 3 to 4 with 8.
- **Data.** Synthetic points: random unit vectors, not embeddings, with Imprint-shaped payloads whose content is this checkout's tracked text in 2,000-character chunks, reused in turn. Real embeddings and a larger vocabulary may change the figures.
- **Readings.** Resident set size from `/proc/<pid>/status`, with its private part (`RssAnon`; the rest is file-backed), 10 seconds after each step: idle with no collections, after a restart, and after 200 filtered searches. Disk is the space allocated under `data/qdrant_storage/`; Qdrant preallocates sparse files, whose apparent size is far larger.

| Collection | Reading | 8 CPUs: resident (private) | 32 CPUs: resident (private) | Storage on disk, 8 / 32 CPUs |
| --- | --- | --- | --- | --- |
| None | Idle | 76 MB (41 MB) | 179 MB (144 MB) | 0 |
| 10,000 points | After a restart | 265 MB (83 MB) | 521 MB (200 MB) | 118 / 117 MB |
| 10,000 points | After 200 searches | 300 MB (83 MB) | 607 MB (227 MB) | |
| 100,000 points | After a restart | 579 MB (248 MB) | 792 MB (395 MB) | 854 / 812 MB |
| 100,000 points | After 200 searches | 673 MB (248 MB) | 894 MB (402 MB) | |

Loading 100,000 points as fast as the server accepted them peaked at 2,064 MB resident with 8 CPUs and 2,441 MB with 32, while the optimizer built the index. Imprint's own ingestion is paced by embedding and was not measured.

A read-only reading of the owner's installed Windows server on 2026-10-25, which runs the same release (its `qdrant.exe` is byte-identical to the one in the verified v1.17.1 archive) on 32 logical CPUs with 132 threads and a default workspace of 4 memories: 115 MB private bytes, a 44 MB working set and a 345 MB peak working set since it started that day. Windows trims working sets, so private bytes is the comparable figure. It is one reading of a lightly used server, not a controlled measurement.

Not measured: macOS, real embeddings, and the daemon under Imprint's own ingestion.
