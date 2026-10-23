---
title: ADR 0019 — Reproducible benchmark evidence
description: Frozen public evidence, paired measurements and auditable statistics for presentation claims.
---

- Status: Accepted
- Date: 2026-10-23
- Related: ADR 0005, ADR 0009, ADR 0010

## Context

The original token experiment depended on an unspecified personal memory index,
ran all OFF trials before ON, permitted working-repository writes and injected a
different system prompt in ON. Its quality judge instructed the model to favor
memory specificity without verifying evidence. Website data was parsed from
rounded Markdown, the run count was inferred, and old quality notes were static.
The original raw trials are absent from this checkout; its published totals
cannot establish a current-version improvement or a confidence interval.

## Decision

Separate local retrieval evaluation from Claude Code task-cost measurement.
Freeze declared public Git sources and clearly synthetic reference/session
fixtures. Hash files, prompts, model/profile settings and harness code. Use a
disposable data directory, dedicated loopback Qdrant ports, pinned workspace,
read-only tools and fresh client sessions with explicit MCP configuration.
Use identical evidence and task instructions in both modes, counterbalance mode
order within paired repetitions, record every failure and retain raw local
event streams. Limit concurrent ONNX processes on laptops. Enforce per-trial
timeouts and spend caps; terminate CLI process groups including MCP children.

Compute totals from complete successful pairs, keeping failures visible; show
per-task medians separately. Count all model/cache token classes. Report actual
resolved model IDs, cache usage, reported list-price cost, latency, tool usage,
and paired hierarchical bootstrap intervals. Missing usage is not zero usage.

Report labeled source recall, MRR, nDCG and boundary checks independently of
generated-answer quality. Lexical reference checks are diagnostic coverage,
not factual correctness. Retire the biased judge and verbosity-based quality
uplift. Do not promise follow-up savings that were never measured.

Publish sanitized trial evidence and versioned JSON summaries; generate website
data directly from JSON, not rounded Markdown. Preserve the existing presentation
keys while adding explicit counts, uncertainty and methodology. Keep the legacy
experiment as historical evidence and label protocol changes; do not describe
different corpora/models as a controlled version-to-version improvement.
Benchmark corpora and trial archives belong to source/website evidence and are
excluded from runtime distribution bundles.

## Alternatives

Rerunning the existing personal index would be quicker but would not establish
equal evidence, stable corpus contents or safe isolation. Replacing the biased
judge with an unblinded judge would retain treatment and position bias. Removing
failed runs without paired accounting would overstate savings. Publishing only
aggregate percentages would prevent recalculation.

## Consequences

These results describe a fixed public task suite and one host, not arbitrary
projects. Indexing uses declared fixed passages to isolate retrieval; it does
not benchmark production chunking/tagging throughput. Provider caches are
observed, not forced cold. Claude costs are estimates rather than subscription
bills. Fresh-session measurements include stdio model startup and do not measure
long-lived multi-turn sessions. The orchestration runner currently requires
POSIX process groups; this does not change Imprint's supported runtime targets.
Publication must retain corpus, provenance, failures and limitations together.

## Validation

Regression fixtures cover all-model/cache accounting, missing/error usage,
paired failure exclusion, pooled totals, seeded intervals and duplicate-source
retrieval scoring. Real-model and Claude trial evidence is recorded in the
benchmark report; unexecuted checks are not represented as passing.
