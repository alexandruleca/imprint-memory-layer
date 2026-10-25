---
title: ADR 0009 — Model profiles and embedding identity
description: Proposed local model profiles, explicit encoding contracts and safe model migration.
---

- Status: Proposed
- Date: 2026-10-23
- Related: [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md), [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md); [local model analysis](../local-model-selection.md)
- Restructured: 2026-10-25

## Context

Imprint mixes code, prose, decisions and transcripts across CPU/GPU desktops. The embedding wrapper assumes generic inputs, first-output selection and mostly mean pooling. It does not apply model-specific query/document prompts or validate the embedding identity of an existing collection. File-hash skipping and content-ID dedup mean a model setting change is not a complete re-embedding operation. Topic prototype caches also lack model identity. The helper pathways can allocate separate chat/tagger models, while the user requests local models with permissive OSS licensing.

## Decision

Evaluate Qwen3-Embedding-0.6B as the primary mixed-retrieval candidate, with Voyage-4-nano as the efficiency challenger, CodeRankEmbed for code-heavy CPU workloads and BGE-M3 as a conservative baseline. Evaluate Qwen3.5-4B as a shared optional helper and 9B for supported 12 GB GPU configurations. Include the current Gemma 4 E4B helper as a baseline; it is Apache-2.0 and should not be confused with the old EmbeddingGemma license. Resolve EmbeddingGemma 2's contradictory licensing/card language and export adapter requirements before selecting it for a permissive profile. No runtime defaults change through this proposed ADR.

Introduce an explicit embedding profile containing upstream/export revisions, artifact hashes, input names/dtypes, output selection, projection/pooling, padding behavior, query/document/task templates, output dimensions and normalization. Store its fingerprint with the collection and include it in model/prototype caches and vector transfer metadata. Reject mismatched identities even when dimensions match.

Re-embed into a separate collection with a verified inventory, preserved provenance and rollback; only promote after evaluation. Existing workspace migration remains a vector-preserving operation and must not be advertised as a model migration. Preserve durable notes and facts. A profile change must bypass stale hash/ID shortcuts when new vectors are needed.

Keep embeddings independent of the optional helper. Prefer one shared local helper process with bounded context, concurrency, structured output, explicit thinking controls and tested tool parsing. Preserve CPU/no-LLM operation. Use runtime and model artifacts whose provenance, licenses and native compatibility are recorded, rather than assuming any GGUF/ONNX file is interchangeable.

## Rejected alternatives

A single large model for all workloads consumes unnecessary background resources. Changing only repository name/dimension can silently corrupt search semantics. Automatically loading multimodal encoders for text adds weight/memory overhead. Keeping separate in-process models is simple but can duplicate memory across clients/workers. A dedicated code embedder may win code-only tests but should not replace a mixed-memory index without evidence.

## Consequences

Implementation requires profile/schema validation, migration, cache and sync work, adapter fixtures and native tests. Benchmarks may select a different final default. Newer model names do not establish better supported-fact extraction or retrieval in this application. Small-machine profiles may keep helper inference disabled. Existing accepted ADRs remain in force until a follow-up change is implemented and this proposal is accepted or superseded.

## Validation

Completed: repository code inspection, current upstream cards/license declarations, hardware inventory snapshot, and bounded ONNX graph-signature inspection for the analysis. No candidate inference, retrieval comparison, helper benchmark or native model test was executed. Adoption requires the held-out evaluation, reference-output parity, resource budgets and migration/rollback checks described in the linked analysis. Proposed quality thresholds are not passing results.
