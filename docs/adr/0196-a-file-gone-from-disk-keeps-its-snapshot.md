---
title: ADR 0196 — A file gone from disk keeps its snapshot
description: When ingest or refresh finds an indexed file gone from a scanned folder, its chunks still leave search, but its snapshot stays, marked with when the file went missing, for ingest.keep_missing_days (365 by default); the mark stays on the device that found the file gone.
---

- Status: Accepted
- Date: 2026-11-22
- Amends: [ADR 0081](0081-verifiable-redacted-source-snapshots.md) — pruning a file gone from disk no longer removes its snapshot
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (pruning only inside scanned roots, unchanged), [ADR 0176](0176-deletions-reach-paired-devices-that-hold-the-memory.md) (a file gone from disk deletes nothing on paired devices, unchanged), [ADR 0195](0195-data-files-are-kept-whole-and-found-by-a-digest.md) (data files restore exactly), [ADR 0197](0197-restore-writes-indexed-files-back-from-their-snapshots.md) (`imprint restore`), [source memory](../source-memory.md)

## Context

The owner rebuilt lost plan files from Imprint's source snapshots after a WSL wipe (ADR 0195's context) and relies on them as a backup. That worked because nothing had refreshed the wiped folder in between. `imprint refresh` prunes an indexed file whose recorded path is missing inside a scanned root (ADR 0005). Pruning went through `vectorstore.delete_by_source`, which deleted the chunks and then called `source_snapshots.remove`, so the full text left this device too. A refresh after the loss, run by hand, by a scheduled job (ADR 0158) or from the dashboard, would have deleted exactly the text the owner needed.

Paired devices keep their copies: a file gone from disk records no deletion (ADR 0176). That helps only when a paired device exists, and only until it refreshes the same files itself.

A snapshot is one head per source, without history (ADR 0081). Pruning is still right for search: a deleted file's code must stop coming back in results.

## Decision

1. **Search forgets the file; the snapshot stays.** Pruning a file gone from disk deletes its chunks on this device as before, and records no deletion for paired devices (ADR 0176). The head stays, with `missing_since` (epoch seconds) added to its metadata JSON. Its version, `updated_at`, manifest and body do not change.
2. **For `ingest.keep_missing_days`.** The setting defaults to 365 and takes 0 to 36500. At every directory ingest or refresh, a marked head is removed once its mark is older than the setting, if this device recorded the file (`device.local_source`) and the file is still absent. `0` removes the snapshot together with the chunks, as before; heads marked earlier go at the next run.
3. **A file that comes back is not missing.** A scan that finds the file clears the mark. The file is then indexed as usual, which replaces the head.
4. **The mark stays on this device.** `export_parts` leaves it out, and a head arriving from sync or a bundle drops it, as an import already sets `source_path_local` false. Scrub, project moves and placement rules keep it.
5. **Tools say the file is missing, not that the index is broken.** `list_sources` lists it with 0 chunks and the date it went missing. `file_summary` says the same. `read_source` adds `missing_from_disk` and notes in its integrity report that the missing chunks are expected.

Single-file ingest has no pruning, so it is unchanged.

## Rejected alternatives

- **Keep the chunks as well, flagged as missing.** Search would keep returning deleted code unless every search path filtered on the flag: dense, lexical, recall, chat and the dashboard.
- **A new snapshot kind, or a newer `updated_at`, for a missing head.**
  - Older devices' `_validate` accepts only five kinds (ADR 0195's rejected alternatives), so they would refuse the head.
  - A newer `updated_at` makes delta sync send the head as a newer version, replacing paired devices' copies of the same text.
  - A key outside the version avoids both. `_validate` ignores unknown keys and has not changed since snapshots were added (commit c356924). `_version` hashes the kind, content hash, indexer profile and manifest shape.
- **Send the mark to paired devices.** It describes this device's disk. A paired device's copy is not missing there, and expiring it there would delete the copies ADR 0176 keeps on purpose.
- **Expire by the head's `updated_at`.** That is when the file last changed, not when it went missing. An old, stable file would expire at the very refresh that found it gone.
- **Keep missing heads forever.** The store of a device that renames and deletes folders would only grow. A setting bounds it, and `0` keeps the old behaviour for anyone who wants deleted files to leave no trace.

## Consequences

- A deleted file stays readable with `read_source`, and restorable with `imprint restore` (ADR 0197), for a year by default. Its compressed text keeps its space in `data/source-snapshots.sqlite3` until then.
- Search is unchanged: the chunks leave at the refresh that finds the file gone.
- Sync sends nothing new and nothing more. A paired device that still holds the chunks sends them back, as before (ADR 0176). They arrive as that device's copies (`source_path_local` false), so pruning never removes them again, and `file_summary` still says the file is missing from this disk.
- Once a head expires here, a paired device that holds it sends it back, as it did right after pruning before this change. It arrives as that device's copy and never expires here.
- A full-workspace import from a release without delta sync, or from a bundle, replaces a head of the same time with the sender's copy. That drops the mark and the local flag, as it already did for every head.
- Workspace migration moves a head only with its chunks (ADR 0081), so a missing head stays in the workspace it was in.
- Only heads whose chunks a refresh removes are marked. An empty file's head has no chunks, is never pruned and is not marked, as before. A file indexed only as legacy chunks has no snapshot to keep (ADR 0081).
- The dashboard's Sources page lists sources from their chunks, so a missing file without chunks is not listed there. `read_source` and the MCP listing show it.
- An older release reads a marked head as an ordinary one: it neither expires it nor labels it.

## Invalidation conditions

- If snapshots gain version history, keep a deleted file as the last entry of its history and retire the mark.
- If the heads of missing files grow beyond a tenth of `source-snapshots.sqlite3` on the owner's devices, lower the default or also bound them by size.
- If paired devices should restore each other's deleted files from the same mark, the mark has to travel with the head; revisit decision 4.
- If deleted files must stay findable by search, index the missing heads separately rather than keeping their chunks.

## Validation

- `tests/test_restore.py`, `MissingFileTests` (10 tests), on the Windows install's Python 3.13 venv from a native copy, with an in-memory Qdrant and `imprint ingest`/`refresh` run on a temporary folder, covers:
  - **Pruning:** the chunks of a deleted file leave, the snapshot stays with the same text, version, `updated_at` and manifest, and the next refresh neither stores nor marks anything again.
  - **Tools:** `list_sources`, `file_summary` and `read_source` label the file missing.
  - **Reappearance:** a file written back is indexed again without the mark; a file back but unreadable is no longer marked and keeps its last good text.
  - **Expiry:** after the set days, unless the file came back; never another device's head.
  - **The setting at 0:** the snapshot goes with the chunks, and earlier marks expire.
  - **Purge** removes a missing head.
  - **Sync:** export leaves the mark out, import drops it, the validator and version accept a marked head, and scrub keeps the mark.
- The suites that touch snapshots, pruning, purge, sync deletions, MCP output, configuration and project associations pass (212 tests). The full suite passes: 1,348 tests, 22 skipped.
- **Not run:** a refresh on the live install, and sync between two real installations with marked heads.
