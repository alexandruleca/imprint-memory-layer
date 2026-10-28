---
title: ADR 0101 — Chat research always reserves an answer
description: Bounded tool research ends in a tools-free synthesis turn, with preserved evidence when synthesis is unavailable.
---

- Status: Amended
- Date: 2026-10-28
- Amends: [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md) — tool-round exhaustion transitions to final synthesis rather than a failed reply
- Amended by: [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) — progress and resource budgets replace the fixed round cutoff and identical-batch test
- Related: [ADR 0061](0061-local-tool-call-formats.md), [ADR 0093](0093-sequential-chat-section-analysis.md)

## Context

A project question reached `chat.max_tool_iters=6` and the dashboard showed “The reply failed.” The tool loop ended immediately after the sixth tool-bearing model turn, before the last results could reach an answer turn. Long-source section analysis does not remove the need to bound tool research; it also does not justify discarding gathered evidence.

## Decision

Give tool research and final synthesis separate budgets. `chat.max_tool_iters` remains a bound on model turns that call tools, not the number of individual calls. Change its default to 12 and validate the configurable range 1–64. Existing explicit settings, including 6, remain effective. Settings names it **Tool research rounds**.

At the bound, emit an informational “Finishing the answer” notice and reserve one model turn with no tools offered. Retain the original question and tool exchanges, remove research-strategy instructions and require an evidence-based answer with sources, uncertainties and missing information. Keep native call parsing active locally to suppress markup from a model that ignores the instruction. Never execute calls proposed in this final turn.

End research early after three consecutive identical read-only call/result batches. Different calls or different results reset that count. Batches containing a write are excluded; this change does not cache, skip or retry writes. If section/context limits prevent more research after tools have run, attempt the same tools-free answer. Initial input, setup and ordinary generation failures retain their error reporting.

If final context preparation or generation fails, yields no answer, or proposes tools again, complete with an explicitly labelled bounded excerpt of this reply's retrieved evidence. Do not present the excerpt as verified synthesis. Preserve already streamed text and ordinary chat persistence. Voice uses a brief spoken fallback; cancellation still stops further work. Section-analysis limits and synthesis token limits remain in force.

## Rejected alternatives

- Remove tool limits: a repeating model could run indefinitely, consuming provider calls and local resources.
- Only raise six to a larger number: the same failure would recur at the new boundary.
- Convert the old error to a success notice without synthesis: the last results still would not answer the question.

## Consequences

Research exhaustion is a normal completion path. Longer questions have more room by default, at the cost of potentially more tool/model requests. The reserved final answer adds at most one coordinator request after research ends; existing bounded workers can still prepare its context. Fallback excerpts are incomplete evidence and may require a narrower follow-up. This change does not automatically expand budgets or offer a new continuation permission flow.

## Invalidation conditions

If measured latency or provider cost makes 12 rounds excessive for ordinary project questions, adjust the default while retaining the reserved answer. If real conversations repeatedly exhaust this budget with new useful evidence, evaluate explicit continuation or a different research budget using those traces. If identical read-only results can legitimately require further identical rounds, revisit the repetition heuristic using a regression case.

## Validation

Stubbed local and OpenAI-compatible provider tests cover final synthesis after the last results, research beyond six rounds, multiple calls per round, repeated reads, write exclusion, blocked final-turn mutations, context exhaustion, final-provider failure, cancellation and chat persistence. Settings API tests cover numeric metadata, bounds, persistence, environment override and reset. Native model answer quality and external provider compatibility beyond the existing adapter remain unmeasured.
