---
title: ADR 0005 — Source lifecycle and hybrid retrieval
description: Full-content chunk identities, byte provenance, safe source replacement and bounded rank fusion.
---

- Status: Amended
- Date: 2026-10-23
- Amended by: [ADR 0110](0110-the-scan-follows-no-links.md) — the directory scan keeps files inside the selected root by following no links instead of resolving each file
- Restructured: 2026-10-25

## Context

Prefix-based memory IDs collide for chunks sharing their first 200 characters. Timestamp-only refresh misses edits and source deletion, while deleting old chunks before extraction/embedding can lose the last usable index. Semantic similarity alone can miss exact identifiers and is not answer confidence.

## Decision

Hash the complete stripped content, project, source and optional chunk position into the public logical ID. Keep existing IDs readable; no destructive bulk migration runs at startup. Reindexing a source replaces its legacy IDs with the new identities. Independent historical notes retain their IDs; re-storing one can create a new identity after upgrade. Mixed-version sync compatibility needs separate validation.

Use the same indexing pipeline for ingest and refresh. Record SHA256, byte size, absolute source path and mtime. Verify the fingerprint before/after extraction, compare hashes, and prune missing recorded paths only inside successfully scanned roots. Reject known secret filenames and symlinks resolving outside the selected root. These rules are not comprehensive secret detection. Empty verified text clears its source; failed extraction, decoding, size checks or embedding retains prior content and reports failure. Default source-text cap is 2,000,000 bytes (`ingest.max_file_bytes`); document extraction retains its separate cap.

Check requested IDs against live storage rather than a process-local dedup cache. Upsert the complete replacement before deleting obsolete chunks, scoped to both project and source. This is recoverable ordering, not a multi-point transaction: readers may see old and new chunks briefly. Inconsistent fingerprints force retry. Do not write separate dependency summaries outside selected roots; dependency manifests use the ordinary file pipeline.

Default search fuses semantic rank with lexical rank using reciprocal rank fusion (constant 60), exact identifier/path/phrase boosts, and deterministic tie breaking. Each channel reads at most 256 candidates. Semantic-only mode remains available. Apply workspace/project/type/tag/source filters before ranking. Expose match type, semantic score and source provenance; never label similarity as calibrated confidence. The lexical channel is bounded and is not BM25 or a complete global text ranking. A failed embedding model still fails hybrid search; lexical-only degraded service is future work.

## Rejected alternatives

Mtime alone is cheaper but insufficient for correctness. Delete-then-store risks data loss. An all-or-nothing generation pointer would give stronger read isolation but requires a larger schema and sync migration. A learned reranker/BM25 can improve quality, but first needs a held-out evaluation set and latency budget.

## Consequences

Hashing adds filesystem reads. Existing file records without fingerprints are reprocessed on the next ingest/refresh. No watcher is enabled automatically. Bounded candidate selection can miss a lexical hit in a very large corpus; pagination is not a stable snapshot during concurrent writes. Stored provenance supports review but does not verify a live file at retrieval time.

## Invalidation conditions

- If storage gains multi-point transactions, or the deferred generation pointer is adopted, replace upsert-before-prune with an atomic swap.
- If a held-out evaluation set shows a learned reranker or BM25 improving retrieval within a latency budget, the precondition this record set, revisit default ranking.
- If corpora grow until the 256-candidate cap measurably drops lexical hits, revisit the cap or add a global text index.

## Validation

Regressions cover prefix collisions, identical chunks at distinct positions, failed/incomplete embedding batches, scoped replacement, timestamp-preserving edits, empty/deleted files, ID lookup and deterministic fusion. Native Qdrant plus the default ONNX model exercises exact identifiers, filters and workspace isolation. See platform validation for executed environments; these fixtures do not establish production recall improvements.
