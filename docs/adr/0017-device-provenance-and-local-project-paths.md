---
title: ADR 0017 — Device provenance and local project paths
description: Keep shared knowledge portable while separating its origin from device-local file references.
---

- Status: Accepted
- Date: 2026-10-23
- Related: [ADR 0006](0006-durable-mcp-and-preserved-client-config.md), [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md), [ADR 0015](0015-workspace-roots-and-source-purge.md)
- Restructured: 2026-10-25

## Context

Projects live at different absolute paths on different devices. Shared conversation chunks and decisions must remain useful everywhere, while agents must distinguish their current device/cwd from a memory's origin. Interpreting a foreign path locally can cause incorrect reads, edits or pruning.

## Decision

Reuse the existing `device_id.txt` sync fingerprint and provision it exclusively across Go/Python. New memories record device ID/name/OS and recorded time. File provenance includes its source device and safe project-relative path. Add a JSON origin column to graph facts through an additive SQLite migration. Unknown legacy origins are never guessed as the receiver.

Preserve origin through CLI/dashboard streams, graph copy/move and snapshots. Imported paths lose local verification. Device identity does not affect logical IDs, embeddings, collections or retrieval filters: shared project memories and old conversations remain searchable everywhere with the same embedding profile.

Store workspace-scoped local project mappings in the policy registry. Ingestion sets the mapping; `workspace map <project> <directory>` sets it explicitly within approved roots. Resolve relative paths under the mapped root and current process cwd, rejecting traversal/symlink escape. Keep original paths as references. Offer a local hint only for an existing approved file inside cwd; verify live contents before editing. Do not rewrite paths inside historical text.

Expose current device/cwd in the MCP handshake and `device_context`, and include it with historical-path guidance in dashboard chat's system context. Expose provenance/path status in retrieval and dashboard detail. Freshness, pruning and physical-path purge use only locally recorded paths. Source-key cleanup also handles foreign/legacy data.

## Rejected alternatives

Per-device collections/filters fragment shared knowledge. Rewriting historical paths changes commands and meaning. Matching absolute strings do not prove local identity. Relabelling imports destroys provenance. Copying root maps incorrectly applies another machine's layout.

## Consequences

Metadata does not require re-embedding. Reindexing old local files supplies provenance; legacy/older-peer data remains accessible with unknown origin. Device identity and maps are not transferred. Whole-data-directory clones need a distinct identity before use on another device. The fingerprint is descriptive metadata, not cryptographic authentication. MCP cwd comes from its host process, not a guessed editor folder. A mapping/file's existence does not verify its current contents.

## Invalidation conditions

- If device identity is ever used to authorize rather than describe, the descriptive fingerprint must be replaced by a cryptographic identity; the record states it is not authentication.
- If MCP hosts start reporting the editor's project folder rather than their own process's cwd, current-device context should use it.

## Validation

Tests cover concurrent Go/Python identity, origin-preserving conversation/vector/fact sync, legacy graph migration, unknown imports, Windows-to-Linux mapping, cwd boundaries, traversal and foreign-path pruning exclusion. Browser coverage distinguishes origin/current device and foreign/local paths. Hosted results belong in platform validation.
