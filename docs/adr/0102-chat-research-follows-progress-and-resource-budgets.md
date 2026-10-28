---
title: ADR 0102 — Chat research follows progress and resource budgets
description: Evidence novelty and reply-wide time and token reservations replace the fixed tool-round cutoff while preserving final synthesis.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0101](0101-chat-research-always-reserves-an-answer.md) — time, token reservations and evidence novelty replace the fixed round cutoff and identical-batch repetition test
- Related: [ADR 0093](0093-sequential-chat-section-analysis.md)

## Context

The user challenged whether a round count can distinguish a loop from useful research and requested a progress-based mechanism. The first correction reserved an answer but still ended research at 12 rounds. Identical consecutive call batches also missed alternating cycles and different queries returning the same evidence.

## Decision

Remove the active `chat.max_tool_iters` setting and fixed round-count termination. Leave old config entries untouched but ignore that key and `IMPRINT_CHAT_MAX_TOOL_ITERS`. Research can continue for any number of rounds while it adds evidence and stays within its reply-wide resource budgets. Settings exposes:

- `chat.research_timeout_sec`: 300 seconds, configurable 10–3600, starting after provider/model setup.
- `chat.research_token_budget`: 65,536 token reservations, configurable 1,024–2,000,000.
- `chat.research_no_progress_rounds`: 4 consecutive read rounds without new evidence, configurable 2–20.

Reserve formatted prompt tokens plus the full maximum output allowance before every research model request, including section workers. Local prompt counts use the loaded tokenizer and template. Remote counts retain the UTF-8-byte estimate used for context planning. Repeated prompt processing counts again. Unused output reservations are not refunded; this is conservative accounting, not provider billing or a guaranteed monetary cap.

Track normalized evidence across calls and rounds. Result order, similarity scores, known timestamps and changed queries alone do not establish progress. Bounded paragraph/word-shingle fingerprints detect repeated evidence and nonconsecutive cycles. Empty reads and tool errors, including changing errors, do not reset patience. A batch containing a write is excluded from the no-progress stopping heuristic; writes are not deduplicated, skipped or retried based on similarity. Time and token budgets still apply to those rounds. Novelty is a heuristic, not a truth or relevance classifier; source text remains untrusted evidence.

Resource exhaustion, stagnation and a full research context all transition to the existing tools-free final answer. The final coordinator request has separate `chat.max_tokens` output space and a 120-second deadline. Final context preparation may use remaining research worker resources; if it cannot fit, retain the existing labelled evidence fallback. A normal answer produced before a stopping condition completes immediately. Cancellation remains effective.

Check time before starting model requests and tools and while model streams advance. Pass deadlines to local generation and request timeouts to remote adapters. An executing blocking tool or native prompt prefill is allowed to return; do not abandon a mutation running in a background thread. Pending calls in the same model batch get explicit “not executed” tool results after the deadline, preserving protocol completeness without starting more actions.

## Rejected alternatives

- Increase or adapt a round-count cutoff: useful evidence can still be cut off solely because it took more steps.
- Remove every resource budget: novel but irrelevant results or endlessly changing writes could otherwise run indefinitely.
- Count only visible output tokens: schemas, repeated prompts, hidden call markup and section workers consume resources too.
- Count every distinct query as progress: alternating queries and recurring tool errors can continue without adding evidence.

## Consequences

Longer useful research is no longer rejected at six or twelve rounds. Users can adjust duration, token reservations and patience independently. Large prompts or worker-heavy inputs can consume the token budget in relatively few requests. Remote token accounting is approximate; the provider's actual tokenizer, retries and billing may differ. Deadline enforcement is cooperative, not a hard wall-clock process kill. In-flight blocking calls may exceed the selected duration; after they return, no more research work starts. Fingerprints are bounded and lossy, so novelty may be misclassified; the model still decides whether evidence supports an answer.

## Invalidation conditions

If real questions stop too early because repeated useful evidence is misclassified, add regression traces and revise normalization or patience. If measured provider usage diverges materially from the estimates, add provider usage reconciliation or tokenizer support before describing the budget as exact. If blocking tools routinely overrun deadlines, add cancellation contracts to those tools before promising hard execution deadlines.

## Validation

Local and remote stub-provider tests cover continuing beyond 12 rounds, unchanged/alternating evidence, changing errors, writes, token and time stopping, pending-call suppression, cancellation, reserved synthesis and fallback persistence. Section-worker integration checks that every worker request is charged. Settings API tests cover defaults, numeric metadata, bounds, override and reset. Native model quality and live remote-provider billing remain unmeasured.
