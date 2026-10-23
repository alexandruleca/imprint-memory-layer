---
title: ADR 0010 — Qwen defaults and explicit reindex
description: Adopt local Apache-2.0 model defaults with encoding identity, bounded helper inference and explicit rebuilding.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0004, ADR 0005, ADR 0007; scoped implementation following proposed ADR 0009

## Context

The user approved adopting the model recommendations and will rebuild the index. A transactional model migration and comparative retrieval study are therefore not prerequisites for this change. Merely changing model names would leave incorrect Qwen pooling/prompts, stale prototype caches and old vectors skipped by content hashes/IDs. Separate helper defaults also duplicate weights and use ineffective thinking control.

## Decision

Default to the pinned Qwen3-Embedding-0.6B quantized ONNX export, 1024 dimensions, 2048 tokens, CPU and conservative batches. Implement query instructions, last non-padding pooling, empty decoder KV inputs, named output selection, optional supported dimension truncation, finite/nonzero checks and L2 normalization. Verify the default graph's SHA256 and pin tokenizer/graph revisions. Persist encoding identity in Qdrant collection metadata and prototype cache names. Reject incompatible or nonempty unversioned collections rather than mixing vectors. Require matching identity for precomputed sync and snapshot imports.

Use Qwen3.5-4B Q4_K_M as the default optional local helper for chat, tags and summaries, with an 8192-token context. Pin/check the managed default GGUF. Share identical model configurations and serialize inference within a process. Use the embedded chat template with thinking disabled and schema grammars for classification/summaries. Bound actual prompts as well as estimated input sizes. Existing explicit user settings remain authoritative; LLM tagging and summaries stay disabled until enabled.

Provide `--force` for ingest/refresh, including single files and queued commands, bypassing unchanged-file and vector-ID dedup. Keep upsert-before-prune semantics and propagate indexing failure through the CLI. Model changes require rebuilding in a fresh workspace or an explicitly backed-up/wiped collection. This change does not run a user's rebuild or alter client profiles.

Preserve validated logical IDs in precomputed transfers. A native sync round trip exposed that a note's payload position zero could produce a different chunk ID on import; accept the supplied ID only when it matches the content/provenance identity with or without position.

## Alternatives

Keeping Gemma and the older Qwen helper avoids a rebuild but misses the accepted model direction. An automatic in-place relabel would leave mixed vectors after interruption. Transactional generation migration and a shared model server provide stronger lifecycle/memory controls but are deferred; existing local-server providers remain available. Large automatic contexts or GPU embedding defaults consume laptop headroom without measured benefit.

## Consequences

Upgrading an unversioned index now requires explicit rebuilding. Manually stored memories must be preserved/reimported separately from source files. Legacy vector sync/snapshots cannot bypass the model contract. Separate processes or different local settings can still duplicate helper allocations. Custom graph/GGUF contracts and licenses require operator validation. These are selected defaults, not demonstrated benchmark winners; ADR 0009's broader evaluation/transactional-migration proposal remains open.

## Validation

Regression checks cover pooling/padding, prompts, empty decoder caches, output/shape/normalization, same-dimension identity mismatch, forced embedding of unchanged sources, precomputed identity, shared helper loading, actual context bounds and structured-generation options. Real pinned Qwen q8 CPU inference passes normalized 1024-dimensional documents/queries and a small relevant-document smoke check on Linux x64/WSL2. The pinned helper also passes local CPU classification, structured summary and dashboard tool/result/answer checks with an 8K context. Platform and helper execution results are recorded in [platform validation](../platform-validation.md). Retrieval-quality comparisons and native Windows/macOS/GPU inference are not established by these tests.
