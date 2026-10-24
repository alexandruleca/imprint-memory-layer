---
title: ADR 0051 — Synced tags follow the latest retag
description: Each memory carries a tagged_at stamp; sync gives a memory both devices hold the classification that was set last.
---

- Status: Accepted
- Date: 2026-10-24
- Related: ADR 0017, ADR 0018, ADR 0020

## Context

Sync deduplicates memories by logical ID (ADR 0018). The ID covers content and provenance, not classification.

`imprint retag`, and LLM tagging during ingest and learn, rewrite a memory's `tags`, `type` and `llm_tagged` in place. When device B retagged a memory that device A created:

- B's paired-workspace digest changed and sync sent the workspace to A.
- A skipped the memory because it already had that ID.
- Both devices then recorded the new digests, so nothing triggered another transfer.

The devices kept different tags for good. Export also omitted `llm_tagged`, so a received memory looked untagged and was tagged again.

## Decision

A memory's content and vector stay immutable. Its classification (`tags`, `type`, `llm_tagged`) can change and must converge, ordered by a payload stamp, `tagged_at`:

- **Writing.** Tagging an existing memory goes through `vectorstore.set_classifications`, used by both `imprint retag` and the LLM tagging pass. It stores the normalized tags and stamps `tagged_at = max(now, previous + 1 ms)`. So a local retag always supersedes the classification it replaced, even when this device's clock runs behind the one that set it.
- **Sending.** Export carries `llm_tagged` and `tagged_at` when they are set. A memory never tagged after creation carries neither, so its exported record and the workspace digest are unchanged.
- **Receiving.** `store_batch_precomputed` reads each existing memory's stamp in the same lookup it uses for IDs:
  - A received classification replaces the local one only when its stamp is strictly newer. The sender's stamp is stored unchanged, so both devices end up with identical payloads and digests.
  - A new memory keeps its received stamp.
  - Import statistics count these memories as `updated` ("retagged" in the CLI and dashboard).

Only the classification travels. Project placement stays per device, as ADR 0036 decided.

## Alternatives

- **Re-upsert every received record.** That would overwrite local project placement and provenance, and turn every sync into a full rewrite.
- **Wall-clock time of the write only.** A device whose clock runs behind would lose its own later retag to the stamp it had just received. Stamping past the previous value avoids that without a vector clock.
- **Lamport or vector clocks per memory.** With one classification field and last-writer-wins semantics, the max-stamp rule gives the same causal order for a device's own edits. Concurrent retags of one memory on two devices before they sync are resolved by time, and that tie needs no more machinery.
- **Tie-break equal stamps by content.** Equal stamps only occur for memories neither device retagged after this change. Overwriting those could replace a retag made before the upgrade with the creation tags.

## Consequences

- A retag on any paired device reaches the others at their next automatic sync, and the devices converge.
- After upgrading, memories retagged before this change keep whatever tags each device has until one of them retags it again.
- Concurrent retags of the same memory on two devices settle on the later stamp. Clock skew can only reorder edits that were truly concurrent.
- A peer on an older release ignores the new fields and keeps skipping known memories, so it does not receive retags.

## Validation

- Storage tests:
  - a newer classification replaces the local one;
  - an older or equal one is ignored;
  - a local retag on a clock far behind still stamps past the received stamp.
- Round trip between two workspaces standing in for two devices:
  - device B's retag reaches device A with identical payloads;
  - syncing again in either direction changes nothing;
  - a later retag on A, made with a clock behind B's, reaches B.
- Full Python suite: 343 tests pass.
