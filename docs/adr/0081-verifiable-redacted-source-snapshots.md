---
title: ADR 0081 — Verifiable redacted source snapshots
description: Store one complete redacted readable source with a versioned character-span manifest beside searchable chunks and expose bounded verified source reads.
---

- Status: Accepted
- Date: 2026-10-26
- Amends: [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md) — completion checks include the source profile and published manifest
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0015](0015-workspace-roots-and-source-purge.md), [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md), [ADR 0060](0060-redact-secrets-before-memories-are-stored.md), [ADR 0082](0082-retain-readable-conversation-evidence.md), [source memory](../source-memory.md)

## Context

The owner asked whether conversations and ingested code could be rebuilt without gaps. The device audit found complete sets of expected legacy chunks for 169 current conversations, but old parsing and minimum lengths omitted readable events and tiny files. Chunk strings alone have no canonical length, offsets or version against which to prove completeness. Search excerpts and chunk pagination can hide stored material too. A full source also needs the same workspace, redaction, purge and sync boundaries as chunks.

## Decision

Store one compressed redacted readable source head per workspace/project/source in a shared SQLite database, with a SHA256 content hash, source version and ordered chunk manifest. Prepare and redact the full text before deriving contiguous character spans; prefer paragraph/line boundaries with bounded overlap and no minimum text length. Verify exact reconstruction before vector writes. Publish the SQLite head after successful vector replacement. This introduces no cross-database transaction or source history.

Use the span splitter for `chunk_file` and `chunk_conversation`; retain legacy Chonkie helpers for direct callers. File prefixes are derived context outside each span. Offsets count Unicode characters in redacted text. The existing normalized logical ID scheme stays; source replacement refreshes exact content for whitespace-only changes that reuse an ID.

Expose read-only version-pinned `read_source` pagination and an explicit index integrity report. Include profile/manifest validity in modern refresh and conversation completion checks. Legacy unchanged files require a forced refresh to acquire snapshots; conversation learner version 3 causes one new reading.

Carry source frames and span fields through existing encrypted live sync and local bundles. Validate bounded checksummed transfers, re-redact on both ends and publish complete envelopes only. Source heads use their update time for last-write selection, while points retain existing merge semantics. Purge exclusions and the write guard apply to snapshot publication. Project moves, workspace migration, scrub, source deletion and wipe handle source heads too; partial workspace copies must not disclose unselected source content. Explicit single-memory deletion invalidates its full source head.

## Rejected alternatives

- **Reassemble only from legacy chunk order.** The audit showed that expected chunks can all exist while parsing already excluded evidence; no original canonical coverage can be proved.
- **Read the original file on expansion.** It may have changed or belong to another device, and would extend memory access into filesystem authority.
- **Keep AST/semantic chunks without a full-source manifest.** Native AST chunks covered ordinary files in the audit, but fallback preambles and tiny files were lost. The span path provides one measurable coverage invariant without pre-embedding model inference. General retrieval quality versus syntax boundaries remains an open measurement.

## Consequences

Exact reconstruction is now a verifiable property of stored redacted readable text, including indentation and line endings. More disk space and transfer bytes are used. A chunk can cut a function, and no evidence yet establishes overall retrieval improvement from the boundary change. The 64 MiB source and 100,000-entry manifest limits fail visibly before a replacement starts. A new head is atomic in SQLite, but interruption between databases can leave index/snapshot disagreement; integrity reports and retry expose and repair it. Sync retains stale point extras and clock-skew limitations. See the [measurement](../../benchmark/source-memory/README.md) for corpus-specific costs and limits.

## Invalidation conditions

- If a held-out source retrieval evaluation shows materially worse evidence recall from line/paragraph spans, use syntax-aware boundary suggestions while preserving exact coverage.
- If measured manifest storage, transfer time or verification scans exceed operational budgets, introduce incremental manifest checks or source-addressed blobs with equivalent purge isolation.
- If concurrent replacement requires readers to see one atomic generation across both stores, replace the two-store publication sequence with a generation protocol.
- If source-head clock skew causes incorrect peer selection in practice, replace wall-clock ordering with a causal source revision protocol.

## Validation

Native macOS arm64: `tests/test_source_snapshots.py` exercises exact reconstruction, interrupted replacement and retry, deletion/integrity, workspace isolation, pinned MCP pages, redaction, source freshness, partial migration and live frame import. The paired preparation run reconstructs 171 local conversation representations and 594 supported repository files. The Go suite passes for lifecycle commands on this host. Tests use disposable profiles and in-memory Qdrant; no production reindex was run. Full-suite results and remaining platform limits are recorded with the change. Windows/Linux execution, actual installers and real paired-device interoperability are unverified.
