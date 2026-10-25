---
title: Benchmark methodology and evidence
description: Reproducible retrieval and Claude Code task measurements with provenance, failures and uncertainty.
slug: docs/benchmarks
---

## Assessment of the original experiment

The previous `BENCHMARK.md` describes Imprint 0.5.1, Sonnet and a 5,427-memory
personal index. This checkout contains its tables but no original raw trials.
The website regenerated today's timestamp while reusing those old numbers.
That timestamp was a build time, not a measurement date.

The harness also used fixed OFF-then-ON order, ambiguous model aliases, different
system prompts, broad tools that could modify the real checkout, and an
unversioned local corpus. Some tasks offered memory-only history to ON while
OFF lacked it. Its judge favored ON specifics and verbosity without seeing
ground truth; judge failures became a neutral score. Per-field medians did not
necessarily sum to a median total. The overall token/cost totals summed per-task
medians rather than all 150 trials; run counts were inferred by the site.

Historical tables remain in `benchmark/legacy-v0.5.1.md`. They are not a current
quality or version-comparison claim. ADR 0019 establishes the replacement.

## Two independent measurements

**Retrieval:** `benchmark/retrieval.json` declares queries and graded relevant
source paths before measurement. Compare production semantic and hybrid search
on the same frozen public index. Recall@k counts distinct relevant sources in
the first k returned chunks. MRR uses the first relevant chunk's rank. nDCG
credits the first occurrence of each relevant source; duplicate chunks earn no
additional gain. Labels identify relevant files, not exact answer spans or
exhaustive relevance for every possible question. Negative filter/workspace
cases are reported as boundary checks, not mixed into positive-query recall.

**Claude task cost:** `benchmark/prompts-v2.json` covers information, rationale,
debugging, cross-project patterns, synthetic session recall and code generation.
Both modes see exactly the same public source snapshot and synthetic reference
project. Both have Read/Grep/Glob, with no shell, network, writes, subagents,
personal memory, auto-discovered client instructions or hooks. ON additionally
has read-only Imprint MCP tools and a brief memory-first instruction matching
the intended integration workflow, with file fallback. This measures guided
memory use, not whether an unguided model elects to use an available MCP server. Their schema,
context and model startup costs are part of the treatment. Code is returned,
not executed: this is not a coding-success benchmark.

Trials are fresh sessions, paired by task/repetition, counterbalanced in mode
order, and scheduled with a recorded random seed. A single ONNX/MCP process at a
time protects laptop memory. Total worker count is recorded. End-to-end wall
latency includes client/MCP startup; local retrieval timings run serially after
an explicitly recorded model-load/first-query measurement.

## Accounting and statistics

Record all resolved model IDs and sum input, output, cache-creation and cache-read
tokens across models. Prefer the CLI's total reported cost; it is a list-price
estimate, not an invoice. Keep provider cache policy visible rather than claiming
a controlled cold-cache experiment. Errors, timeouts, missing usage and failed
MCP connections are failed trials. Never interpret them as free successful
answers. A failed or missing side excludes both members from paired totals.
Every attempted trial, including failures, stays in the evidence.

Overview percentages use actual pooled paired totals. Per-task tables show
medians and should not be summed as if they were observed usage. Uncertainty is
a seeded 2,000-sample paired hierarchical bootstrap: resample task IDs, then
paired repetitions within each selected task. The interval concerns this fixed
suite; five repetitions do not establish a general population estimate.

Reference regex coverage is a useful diagnostic of named concepts, not an
answer-quality score. No claim of factual accuracy, quality uplift, retrieval
confidence, or avoided follow-up turns follows from it. Quality needs blinded
evidence-based human review or an independently validated judge.

## Run and reproduce

Use Python 3.12 with `requirements/base.lock.txt` and `requirements/cpu.lock.txt`, an authenticated Claude Code
CLI supporting restricted mode and explicit per-session settings, and a POSIX host. No setup or real memory
directory is used. The default embedding profile downloads once into the
disposable run directory. The runner selects independent loopback Qdrant ports.

Safe mode suppresses explicitly configured MCP servers in the tested client, so
the harness uses restricted mode with explicit hooks/memory/instruction settings
instead. Snapshots live outside the checkout's ancestor instruction files.
Organization-managed policy can still apply equally to both modes. These
controls follow the [client settings](https://code.claude.com/docs/en/settings)
and [memory controls](https://code.claude.com/docs/en/memory); CLI initialization
traces must confirm only the expected tools and MCP connection are present.

```bash
PYTHON=/path/to/venv/bin/python bash benchmark/run.sh --runs 5 --workers 2
# Pin an exact Claude model ID for a publication run; aliases are recorded too.
PYTHON=/path/to/venv/bin/python bash benchmark/run.sh --model MODEL_ID --subset

# Resume the SAME experiment; changing model/tasks/repetitions/harness is refused.
python benchmark/run.py --resume benchmark/results/RUN_ID --model MODEL_ID

python benchmark/runtime.py retrieval --state benchmark/results/RUN_ID/state.json \
  --out benchmark/results/RUN_ID/retrieval-report.json --runs 5
python benchmark/summarize.py benchmark/results/RUN_ID --publish
# Recalculate a committed evidence bundle without Claude, Qdrant or model weights:
python benchmark/summarize.py benchmark/published/RUN_ID
python benchmark/runtime.py stop --state benchmark/results/RUN_ID/state.json
```

Raw event streams, model files, storage and local paths stay in ignored
`benchmark/results/`. Published manifests, sanitized answers/metrics, retrieval
trials and JSON summaries live in `benchmark/published/RUN_ID/`. `latest.json`
selects the measured run for the site. The adapter preserves existing
`overall`, `categories` and `env` fields and adds counts, confidence intervals,
retrieval metrics and methodology. The timestamp is the measurement-report
timestamp, never a fresh site-build timestamp.

The default overall cap is $30 of reported cost and $0.50 per trial; each trial
has a timeout. A cap can leave an incomplete experiment, which cannot be
published as a full suite. Budget guards reserve headroom for concurrent calls;
they are controls, not a billing guarantee. Failed trials are not overwritten
on resume. Model initialization/download and indexing costs are separate from
per-task Claude usage. Fixed 1,800-character passages with 180-character overlap
isolate retrieval from chunking/tagging changes; this is not end-to-end ingest
throughput evidence.

## Presentation integration

`site/scripts/build-benchmarks.mjs` validates and copies measured JSON directly.
`generatedAt` remains the last trial's measurement timestamp for compatibility;
`calculatedAt` records statistics recomputation. Rebuilding the site does not
make old data look newly measured. Render `runCount`, `validPairs`, `failedRuns`,
`repetitions`, `modelIds` and `sourceRevision` from data rather than constants.

`overall.tokensOff/On` and `costOff/On` are pooled paired totals. Category `rows`
are per-task medians; `avgTokens/avgCost` are category aggregate changes retained
for the existing chart contract. `overall.qualityPct` and category `quality` are
null deliberately. Do not restore historical quality claims or infer downstream
savings. Both positive and negative deltas must be displayed. The bootstrap
intervals may include no difference; preserve that uncertainty in the copy.

The raw-event audit in `scripts/benchmark_evidence.py` independently checks saved
usage against CLI result events, verifies snapshot hashes and exports an
allowlisted per-model usage ledger and tool-result character counts. It never
exports credentials, full initialization events or private local paths. Full
traces remain local. Character counts are not model token attribution. Published
corpora/trials are excluded from runtime installer bundles.

The manifest's `harnessRevision`/`harnessSha256` identify the measurement code;
the report's `analysisSha256` identifies the statistics code used to recalculate
results. Post-measurement validation can reject malformed usage without changing
the original valid trials. Publishing refreshes the canonical `BENCHMARK.md` as
well as the website selector and versioned evidence bundle.

## Role on the presentation site

The homepage leads with persistent project memory, local retrieval, encrypted workspace sync and continuity across tools and devices. Benchmark details live on the evidence page, linked from the footer, rather than serving as hero statistics or a primary navigation item. Neither token savings, cost non-regression nor better answer quality is a product guarantee: report the measured costs, increases, uncertainty and limits together.

## Comparing response-budget changes

New manifests record `runtimeRevision` and `runtimeSourceSha256` separately from `sourceRevision` (the frozen public corpus). This allows unchanged task evidence to exercise a newer MCP runtime without calling the corpus commit the implementation version. Resuming refuses a different runtime hash. Keep both versioned result bundles. Runs taken at different times remain subject to provider cache and service variation; compare paired OFF/ON effects within each run rather than treating a sequential version comparison as randomized causal evidence.

## Follow-up bundle and comparison

The compact-response follow-up is `20261023T140134777248Z-fdea98ae-s1729`: 180 successful trials, 90 pairs, no protocol tool errors, plus 460 retrieval searches and 60 explicit boundary checks. Runtime `ee040e6` is pinned separately from the unchanged `fdea98ae` corpus. Later sync-control recovery and HTTP-client lifetime fixes leave MCP response formatting and ranking behavior unchanged; the measured runtime revision remains explicit.

The selector retains `previousRunId` so the evidence page can display both complete studies. Comparison is permitted only with identical corpus, tasks, resolved model, repetition count and embedding identity. The table is descriptive; provider caches were managed externally and the runs were sequential. See [context budgets](context-budgets.md) for the actual response/file-read tradeoff.

```bash
python scripts/benchmark_evidence.py RAW_RUN --out benchmark/published/RUN_ID/usage-diagnostics.json
python scripts/benchmark_comparison.py benchmark/published/BEFORE benchmark/published/AFTER \
  --out benchmark/published/AFTER/comparison.json
```

Both token/cost and retrieval aggregates were recalculated independently from the sanitized published rows and matched the raw-run report exactly. All 51 frozen source hashes and 180 raw usage ledgers were audited. Private CLI event streams and machine-specific paths remain outside the publication bundle.

## Recall lookups

`benchmark/runtime.py recall` measures [recall](mcp.md#recall-several-reads-in-one-call) ([ADR 0058](adr/0058-recall-batched-reads-with-optional-local-planning.md)). It runs eight multi-part lookups from `benchmark/recall.json` against the frozen public index. Each lookup has a question, a plan of 2–4 read steps and gold sources declared beforehand. Every lookup runs in each of these modes:

- **sequential**: the plan's steps as separate tool calls with their default output sizes;
- **recall_plan**: one recall with the plan at the default 4,000 characters;
- **recall_plan_matched**: one recall with the plan, given exactly the characters the sequential calls returned (capped at 12,000);
- **recall_intent_off**: recall with the question only and no model, which is `search`;
- **local-model modes**: with Qwen3.5-4B on CPU, once with a 600 s timeout to see natural latency and once with the 30 s default.

Caller tokens count tool-call arguments plus results with the index's Qwen3 tokenizer, a proxy. Server time excludes the caller model turn each extra round trip costs. "Gold visible" counts declared gold sources named in the response: for condensed answers, only cited evidence names its source. This is coverage, not correctness.

Results on 2026-10-25, eight lookups, i9-13900HX under WSL2, CPU only. Evidence: `benchmark/published/recall-20261025T081236Z/`.

| Mode | Round trips | Caller tokens | Gold visible | Server time |
| --- | ---: | ---: | ---: | ---: |
| sequential | 21 | 19,288 | 23/26 | 1.66 s |
| recall_plan (4,000 chars) | 8 | 10,681 (−45%) | 21/26 | 1.36 s |
| recall_plan_matched | 8 | 18,622 (−3%) | 24/26 | 1.34 s |
| recall_intent_off (= search) | 8 | 8,955 | 17/26 | 1.19 s |
| local model, intent only, 600 s | 8 | 5,286 | 15/26 | median 46.7 s per lookup |
| local model, plan + answer, 600 s | 8 | 5,775 | 17/26 | median 31.7 s per lookup |
| local model, intent only, 30 s | 8 | 9,371 | 18/26 | 30.0–33.0 s, 0/8 answered |
| local model, plan + answer, 30 s | 8 | 10,222 | 21/26 | 23.3–30.0 s, 1/8 answered |

What this shows:

- **Round trips.** One recall replaces 2–4 calls: 21 calls become 8.
- **Tokens and coverage.** At the default budget, the token saving comes mostly from the shared 4,000-character budget, and it shows two fewer gold sources. At matched size, recall uses 3% fewer tokens and shows one more gold source than the separate calls: results merged by id, with each step's best result first.
- **Server time.** Concurrent steps cut server time by about 18%. An earlier run of the deterministic modes under heavier host load measured 19.1 s against 13.0 s, with the same token counts except for the defect below.
- **Local model on CPU, given time.** It planned in 10.7–20.9 s and answered in 24–44 s. One lookup took 512 s and two answers took about three minutes while other work loaded the host.
- **Local model on CPU, at the 30 s default.** It almost never answers: planning usually fits and the answer does not. A timed-out request keeps processing its prompt in the background, which delays the next recall's planning. Recall then returns the deterministic result after about 30 s, plus about a second for the fallback search.
- **Answer quality.** Most of the 16 answers matched their cited evidence and attributed projects correctly. At least two misstated it: Qdrant startup races credited to `start_new_session` instead of the start lock, and an invented fifth tagging layer.
- **Verdict.** On this CPU the local-model path is too slow for interactive use at the default timeout. It needs a GPU or a faster server, which was not measured.

The first full run exposed a defect that the published run excludes: concurrent `kg_query` steps sharing the graph's SQLite connection returned no facts or raised "bad parameter or other API misuse". The graph connection is now held by one thread at a time.
