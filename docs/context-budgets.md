---
title: Context budgets and agent cost
description: Implemented MCP output controls, measured overhead and proposed next experiments.
---

## Where cost comes from

Imprint search embeds and ranks locally; it does not call a paid LLM. Automatic
sync exports existing vectors and hashes snapshots, without generating tokens
or re-embedding. The connected coding agent still pays its own context and
generation costs. Extra tool schemas, repeated evidence, broad workspace
context and follow-up turns can increase that usage even when retrieval is fast.
Optional helper models and dashboard chat are separate workloads.

The first audited public run processed 31.7% more tokens with memory enabled.
Its estimated cost was 5.6% lower, but the 95% interval crossed zero. Search
returned 641,602 result characters across 94 calls, alongside 97 file reads.
Those character counts locate overhead; they are not exact token attribution.
See the versioned [trial evidence](../benchmark/published/) and [methodology](benchmarks.md).

## Implemented controls

- **Focused defaults:** five search results and a 4,000-character response budget,
  with whole result blocks, source IDs, device/path provenance and continuation.
- **Selective evidence:** verbatim windows around query terms instead of only a
  chunk's opening. No LLM paraphrase is inserted into retrieved evidence.
- **Explicit broad context:** compact search does not load unrelated workspace
  wake-up context. `detail="full"` requests that view when useful.
- **Bounded expansion:** `get_memory` defaults to 4,000 content characters and
  exposes `start`/`next_start`; callers can request up to 12,000. Expand selected
  evidence, not every hit.
- **Stop guidance:** the MCP instructions tell agents to stop when evidence is
  sufficient. Compact responses do not encourage graph traversal for every task.
- **Local writes:** normal `store` tagging is deterministic; a configured LLM
  provider alone does not force a paid tagging call.

These changes retain ranking and embeddings. A response budget can hide a useful
result or answer span; follow-up calls can offset its savings. Raw ranking recall
does not establish the quality of the excerpts an agent actually used. The new
paired run must count those calls, failures and all cache/token classes rather
than infer a benefit from output size. Its measured results are in [BENCHMARK.md](../BENCHMARK.md).

## Follow-up measurement

The second full run completed 180/180 trials and 90 pairs with no failures or
protocol tool errors, using the same frozen corpus, tasks, model and repetitions.

| Measure | Previous policy | Compact policy |
| --- | ---: | ---: |
| Processed token change, ON vs OFF | +31.7% | +25.0% |
| Token-change 95% interval | +18.5% to +45.7% | +11.3% to +43.6% |
| Estimated cost change, ON vs OFF | −5.6% | −9.0% |
| Cost-change 95% interval | −20.6% to +10.5% | −19.0% to +2.9% |
| ON search calls | 94 | 90 |
| ON serialized search-result characters | 641,602 | 376,513 |
| ON file reads | 97 | 105 |
| ON file-read result characters | 505,054 | 649,564 |

Search-result character volume fell 41.3%, while file-read character volume rose
28.6%. The agent still falls back to files, so a smaller tool response does not
translate directly into an equal token reduction. ON used fewer recorded turns
than OFF in both runs (434 vs 460 in the follow-up), yet processed more tokens.
Tool-schema/system-prefix overhead and how hosts replay cached context are useful
next hypotheses to instrument; these ledgers do not isolate their contribution.

Token overhead is 6.7 percentage points lower in the later run. Both cost
intervals still cross zero. These sequential studies are subject to provider
cache/service variation: the table does not establish a causal version effect,
a guaranteed saving, or better answers. Source-ranking metrics stayed identical;
they do not validate excerpt completeness or factual correctness. Full-precision
comparison and accounting are in the [published follow-up bundle](https://github.com/alexandruleca/imprint-memory-layer/tree/main/benchmark/published/20261023T140134777248Z-fdea98ae-s1729).

## Proposed follow-up experiments

These are proposals, not implemented features or promised savings:

| Priority | Experiment | What would justify adopting it |
| --- | --- | --- |
| P1 | Multi-turn project sessions | Measure complete tasks, follow-up turns, tool calls and correctness with a long-lived MCP process. The current fresh-session study includes startup but does not measure ongoing conversation efficiency. |
| P1 | Exposed-evidence evaluation | Label answer spans/citations and score what survives the response budget, alongside ranked-source recall. Include stale, contradictory, multilingual and unseen customer cases. |
| P1 | Adaptive response budgets | Compare small lookups, rationale questions and larger code tasks with predeclared budgets. Keep IDs and a reliable expansion route; do not tune thresholds against the published suite. |
| P2 | Group duplicate/adjacent evidence | Group repeated passages or contiguous chunks while retaining every source/device origin needed for attribution. Verify that different projects and historical versions are not collapsed incorrectly. |
| P2 | Batched selected-memory expansion | Fetch a bounded set of IDs in one round trip, with a total budget and individual continuation. Compare total context and tool turns, including the added schema cost. |
| P2 | Smaller client tool profiles | Offer a read-focused profile for clients that do not need ingestion/admin tools. Measure schema and capability tradeoffs in disposable client profiles before changing registrations. |
| P2 | Reuse and invalidation | Cache repeated local query work behind workspace/index-generation keys. This can save local CPU, but it does not itself prove a lower coding-agent token bill. Never reuse results across workspaces. |
| P2 | Background conversation distillation | Compare concise local summaries plus links to the original conversation against raw chunks. Retain provenance, contradictions and original evidence; evaluate factual loss before replacing history. |

Reducing embedding dimensions or sequence length targets local indexing/search
resources. It is not a direct fix for the coding agent's prompt bill and may
reduce retrieval recall. Likewise, necessary live-source verification before
edits is part of a correct task, not waste to remove solely to improve a chart.

Any further integration/default change needs its own evidence and the relevant
ADR. Keep cost, answer correctness, attribution, latency and privacy constraints
visible together; no universal saving or non-regression guarantee follows from
one public suite.
