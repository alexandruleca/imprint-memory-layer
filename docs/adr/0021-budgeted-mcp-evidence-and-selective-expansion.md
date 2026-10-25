---
title: ADR 0021 — Budgeted MCP evidence and selective expansion
description: Compact search excerpts, explicit context expansion and measured token accounting.
---

- Status: Accepted
- Date: 2026-10-23
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md), [ADR 0019](0019-reproducible-benchmark-evidence.md)
- Restructured: 2026-10-25

## Context

The first audited 180-trial run processed 31.7% more tokens with memory enabled.
The estimated cost point difference was -5.6%, with an interval crossing zero.
Search returned 641,602 characters across 94 calls; agents often then read source
files. The current search hydrates several records, attaches broad workspace
context on its first call and suggests graph exploration even for focused tasks.
This duplicates context without establishing better generated answers.

## Decision

Default search to five results and compact evidence, within a 4,000-character
response budget (configurable 1,200–12,000). Pick deterministic query-relevant
verbatim windows rather than always taking the start of a chunk. Preserve IDs,
source provenance, device origin and path status, with bounded previews and a
continuation offset that does not skip budget-hidden results. Character budgets
are not represented as exact tokenizer counts.

Do not attach broad workspace wake-up context to compact searches or encourage
unnecessary graph traversal. Retain the previous context/hydration view as
`detail="full"`, explicitly requested. Keep ranking, embeddings, storage and
workspace filters unchanged. `get_memory` returns a 4,000-character content
window by default, with `start`, `next_start` and an optional budget up to 12,000.
Agents expand only selected evidence and still verify live sources before edits.
Retrieved text remains untrusted evidence, never client instructions.

Record runtime source hash/revision separately from corpus revision for new
benchmarks. Rerun the same public tasks and frozen source evidence with actual
cache/token/cost accounting; preserve the original run. Do not infer a saving,
answer-quality improvement or absence of regressions merely from smaller output.

## Rejected alternatives

Changing embedding models or ranking simultaneously would confound response
format and retrieval effects. LLM summarization of every result adds latency,
cost and a potential unsupported paraphrase. Always returning only IDs would
force extra round trips. Keeping broad first-search context makes a focused
lookup pay for unrelated workspace material. These options remain unnecessary
for this first measured response-budget improvement.

## Consequences

MCP search defaults change; callers needing the previous context must explicitly
request it. Excerpts can omit an answer, so selected evidence remains expandable
and continuation is available. The agent might compensate with more calls; the
new benchmark must measure that rather than assuming fewer tokens. Exact
provider cache behavior and long-lived multi-turn sessions remain uncontrolled.

## Invalidation conditions

- If the paired rerun shows the compact defaults increasing total tokens or cost, for example through extra calls, revisit them.
- If MCP clients expose a tokenizer or token budget for tool results, character budgets can become token budgets.

## Validation

Tests cover late query matches without paraphrasing, hard character budgets,
IDs/provenance, correct pagination, no implicit broad-context query and lossless
selected-memory content pagination. Full paired trials and independent retrieval
checks are published in the benchmark evidence when complete. The latter checks
ranking separately from response formatting and does not establish answer quality.
