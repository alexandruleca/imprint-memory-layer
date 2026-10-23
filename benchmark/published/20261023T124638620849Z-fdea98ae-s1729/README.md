# Imprint benchmark results

Measured 2026-10-23 against `fdea98aea4bb` using claude-sonnet-5-5.

180/180 trials recorded; 90 complete successful pairs; 0 failed trials.

Total processed tokens: +31.7% (2,584,140 OFF → 3,402,984 ON).
Reported list-price cost: -5.6% ($4.3167 OFF → $4.0757 ON).

Token-change 95% interval: +18.5% to +45.7%. Cost-change 95% interval: -20.6% to +10.5%.

Totals sum all complete paired trials. Tables below show per-task medians. These are different estimands.

No answer-quality uplift is claimed. The old biased LLM judge and response-length proxy are retired.

The cost interval includes no change: the point estimate does not establish a clear saving.

## Information

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Chunking system | 5 | 26,208 | 39,153 | +49.4% | $0.0541 | $0.0596 |
| Embedding model | 5 | 26,669 | 53,386 | +100.2% | $0.0554 | $0.0523 |
| Workspace isolation | 5 | 58,444 | 74,108 | +26.8% | $0.0746 | $0.0664 |
| Metadata tagging | 5 | 37,428 | 34,662 | -7.4% | $0.0998 | $0.0359 |
| Qdrant lifecycle | 5 | 24,924 | 37,345 | +49.8% | $0.0485 | $0.0554 |
| Portable device context | 5 | 21,392 | 23,627 | +10.4% | $0.0324 | $0.0413 |
| Scoped source purge | 5 | 25,417 | 46,567 | +83.2% | $0.0506 | $0.0414 |

## Decision Recall

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qdrant vs embedded | 5 | 24,200 | 20,267 | -16.3% | $0.0312 | $0.0273 |
| LLM tagger default off | 5 | 21,438 | 21,779 | +1.6% | $0.0256 | $0.0328 |

## Debugging

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Search returns nothing | 5 | 33,858 | 33,951 | +0.3% | $0.0471 | $0.0414 |
| Slow embedding on first run | 5 | 29,303 | 54,646 | +86.5% | $0.0624 | $0.0608 |
| Encrypted sync boundary | 5 | 32,172 | 57,667 | +79.2% | $0.0765 | $0.0647 |

## Cross-Project

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Async work pattern | 5 | 33,367 | 35,468 | +6.3% | $0.0813 | $0.0469 |
| Config precedence | 5 | 27,326 | 38,396 | +40.5% | $0.0334 | $0.0455 |

## Session Summary

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Recent activity | 5 | 10,735 | 17,679 | +64.7% | $0.0073 | $0.0152 |

## Creation

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qdrant connectivity test | 5 | 20,640 | 23,722 | +14.9% | $0.0336 | $0.0466 |
| Memory count utility | 5 | 19,498 | 23,294 | +19.5% | $0.0201 | $0.0391 |
| Workspace stats CLI | 5 | 19,575 | 32,335 | +65.2% | $0.0267 | $0.0346 |

## Retrieval on the labeled public corpus

| Mode | Recall@5 | MRR@10 | nDCG@10 | Warm p50 | Warm p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| semantic | 0.802 | 0.869 | 0.782 | 108.0 ms | 124.1 ms |
| hybrid | 0.833 | 0.839 | 0.776 | 153.4 ms | 170.1 ms |

Distinct gold sources in the first k returned chunks; duplicate source gains are zero.

## Method and limits

- Actual sums across complete successful pairs; cache input included. Per-task rows are medians, not summed component medians.
- 95% paired hierarchical bootstrap, resampling task IDs then repetitions; 2,000 samples, seed 1729. Describes this fixed suite.
- No answer-quality uplift is claimed. Reference-check coverage is a lexical diagnostic, not verified correctness.
- Provider-managed cache; measured separately, not forced cold. Concurrent trials may share cache.
- Claude Code reported list-price estimate, not a subscription invoice
- Protocol v2 and the fixed public corpus differ from the legacy v0.5.1 experiment; results are not a model/version improvement claim.

See the methodology in `docs/benchmarks.md` and the manifest, sanitized trials and summary alongside this report.
