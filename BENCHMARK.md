# Imprint benchmark results

Measured 2026-10-23 using claude-sonnet-5-5. Runtime `ee040e61a0be`; frozen corpus `fdea98aea4bb`.

180/180 trials recorded; 90 complete successful pairs; 0 failed trials.

Total processed tokens: +25.0% (2,544,880 OFF → 3,182,015 ON).
Reported list-price cost: -9.0% ($4.1253 OFF → $3.7542 ON).

Token-change 95% interval: +11.3% to +43.6%. Cost-change 95% interval: -19.0% to +2.9%.

Totals sum all complete paired trials. Tables below show per-task medians. These are different estimands.

No answer-quality uplift is claimed. The old biased LLM judge and response-length proxy are retired.

The cost interval includes no change: the point estimate does not establish a clear saving.

## Information

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Chunking system | 5 | 25,978 | 38,714 | +49.0% | $0.0539 | $0.0527 |
| Embedding model | 5 | 26,552 | 38,225 | +44.0% | $0.0559 | $0.0443 |
| Workspace isolation | 5 | 58,649 | 52,836 | -9.9% | $0.0656 | $0.0593 |
| Metadata tagging | 5 | 37,501 | 32,961 | -12.1% | $0.0508 | $0.0387 |
| Qdrant lifecycle | 5 | 28,444 | 35,080 | +23.3% | $0.0647 | $0.0490 |
| Portable device context | 5 | 22,928 | 34,630 | +51.0% | $0.0368 | $0.0394 |
| Scoped source purge | 5 | 25,368 | 37,975 | +49.7% | $0.0477 | $0.0475 |

## Decision Recall

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qdrant vs embedded | 5 | 21,031 | 20,499 | -2.5% | $0.0285 | $0.0267 |
| LLM tagger default off | 5 | 21,170 | 21,021 | -0.7% | $0.0337 | $0.0287 |

## Debugging

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Search returns nothing | 5 | 24,425 | 32,931 | +34.8% | $0.0437 | $0.0381 |
| Slow embedding on first run | 5 | 45,176 | 39,643 | -12.2% | $0.0687 | $0.0638 |
| Encrypted sync boundary | 5 | 32,298 | 41,722 | +29.2% | $0.0746 | $0.0697 |

## Cross-Project

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Async work pattern | 5 | 24,367 | 34,528 | +41.7% | $0.0463 | $0.0447 |
| Config precedence | 5 | 26,425 | 31,229 | +18.2% | $0.0382 | $0.0338 |

## Session Summary

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Recent activity | 5 | 10,742 | 17,590 | +63.7% | $0.0063 | $0.0081 |

## Creation

| Task | Pairs | OFF tokens | ON tokens | Change | OFF cost | ON cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qdrant connectivity test | 5 | 20,223 | 55,307 | +173.5% | $0.0313 | $0.0573 |
| Memory count utility | 5 | 19,521 | 22,811 | +16.9% | $0.0234 | $0.0349 |
| Workspace stats CLI | 5 | 30,675 | 31,195 | +1.7% | $0.0360 | $0.0297 |

## Retrieval on the labeled public corpus

| Mode | Recall@5 | MRR@10 | nDCG@10 | Warm p50 | Warm p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| semantic | 0.802 | 0.869 | 0.782 | 107.5 ms | 120.8 ms |
| hybrid | 0.833 | 0.839 | 0.776 | 151.4 ms | 173.0 ms |

Distinct gold sources in the first k returned chunks; duplicate source gains are zero.

## Method and limits

- Actual sums across complete successful pairs; cache input included. Per-task rows are medians, not summed component medians.
- 95% paired hierarchical bootstrap, resampling task IDs then repetitions; 2,000 samples, seed 1729. Describes this fixed suite.
- No answer-quality uplift is claimed. Reference-check coverage is a lexical diagnostic, not verified correctness.
- Provider-managed cache; measured separately, not forced cold. Concurrent trials may share cache.
- Claude Code reported list-price estimate, not a subscription invoice
- Protocol v2 and the fixed public corpus differ from the legacy v0.5.1 experiment; results are not a model/version improvement claim.

See the methodology in `docs/benchmarks.md` and the manifest, sanitized trials and summary alongside this report.
