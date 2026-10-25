---
title: ADR 0015 — Workspace roots and source purge
description: Approved filesystem scope, confirmed overlap, stable write destinations and previewable source cleanup.
---

- Status: Amended
- Date: 2026-10-23
- Amends: [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) — extends its workspace binding policy
- Amended by: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) — the conversation-learning and desktop-export rules; roots still gate filesystem ingestion and refresh
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md)
- Restructured: 2026-10-25

## Context

Separate collections do not prevent ingesting an unrelated project into the active workspace. Global selection can redirect multi-stage writes. Users need file/directory cleanup after mistaken ingestion without wiping unrelated memories. The owner explicitly permits overlapping roots when the user confirms sharing.

## Decision

Store approved canonical directory roots and purge exclusions in a separate local SQLite registry. Transactions serialize first-root claims and overlap checks. The first filesystem ingest binds its directory (a single file binds its parent). Later targets and discovered project roots must stay within approved roots. Additional roots require an explicit add; overlap requires `--allow-overlap` or a dashboard confirmation naming conflicting workspaces. Resolve symlinks and use path-component boundaries. Removing a root changes future ingestion, not existing indexed data.

Capture CLI workspace selection before queue waits/submission and vector write/replacement destinations before embedding and cleanup. Pin each MCP process's default workspace at startup; explicit tool workspace parameters remain available. This extends ADR 0006's binding policy while retaining its original rationale.

Conversation learning requires recorded cwd evidence inside approved roots; skip unscoped/mixed-outside transcripts. Auto-memory imports require that project evidence. Desktop exports require approved locations, with workspace-specific import tracking. Retain physical provenance for cleanup.

Offer preview-default CLI, dashboard and destructive-annotated MCP source purge. Match physical paths or logical source keys; recursive matching uses directory boundaries. Delete only the selected workspace's vectors and matching source-linked graph facts, including source-key orphan facts. Apply first records an exclusion; refresh skips it, and storage/sync import rejects excluded sources until explicitly restored. Serialize final vector upserts against exclusion updates after inference, then enumerate purge matches, so in-flight inference cannot restore a purged source. API/MCP apply must name the reviewed workspace. Maintenance shares the ingestion lock. Purge works on old-model collections without embedding inference.

## Rejected alternatives

Global selection alone permits accidental pollution. Forbidding overlaps prevents intentional shared documents. Silent overlap cannot distinguish sharing from a mistaken target. Substring deletion risks adjacent directories; workspace wipes lose unrelated memories. Allowing refresh to restore purged sources defeats cleanup.

## Consequences

New roots/sharing become deliberate actions. Existing collections are preserved; historical provenance is not automatically converted into ownership. Logical source cleanup handles legacy/foreign paths. Inline/URL memories and intentional migrations still use the caller's selected workspace; roots control filesystem ingestion, not authentication.

Purge is not a cross-database transaction. Partial failures remain visible, preserve the exclusion and can be retried. Exclusions are local policy, not exported access rules. Backups, exported bundles, logs and original files remain intact. Old records lacking physical provenance require logical source cleanup. Restoring an exclusion permits future ingestion but does not recover deleted indexed copies.

## Invalidation conditions

- If every write comes to name its workspace explicitly, for example through persistent per-client workspace binding, the first-root binding that guards against accidental ingestion can be revisited.
- If vector storage and the graph gain a shared transaction, purge can become atomic instead of exclusion-first and retryable.
- If exclusions need to hold across devices, local-only exclusions are insufficient and sync would have to carry them.

## Validation

Tests cover first binding, sibling roots, symlink escape, competing claims, confirmed overlap, cwd filtering, preview immutability, recursive boundaries, old-model cleanup, other-workspace preservation, exclusion/restore and a workspace switch during embedding. HTTP tests enforce explicit workspace/boolean apply values. Browser tests exercise preview/typed confirmation and overlap approval. Record hosted native results in platform validation.

Learning follow-up: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) supersedes the conversation-learning and desktop-export rules above. Learning no longer requires an approved root; roots still gate filesystem ingestion and refresh.
