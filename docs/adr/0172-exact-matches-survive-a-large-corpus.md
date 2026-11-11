---
title: ADR 0172 — Exact matches survive a large corpus
description: The lexical channel reads the points holding every query term and then each term's points from the rarest up, and a query of several terms found verbatim ranks as a third list in the fusion, so an exact title is found and ranked among hundreds of thousands of memories.
---

- Status: Accepted
- Date: 2026-11-11
- Amends: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) — how the lexical channel picks its candidates, and how a verbatim match of the query is fused
- Related: [ADR 0131](0131-meetings-are-recorded-by-channel-and-transcribed-locally.md) (recordings), [ADR 0058](0058-recall-batched-reads-with-optional-local-planning.md) (recall shares this search), [indexing and retrieval](../indexing-retrieval.md)

## Context

On 2026-11-11 a seven-minute meeting recorded in the dashboard was transcribed into `recording/0b85ed4a…` (project `meetings`, two chunks, the first starting with its title) at 10:11:43. At 10:18:00 an agent in Claude's desktop app searched for its title, `Meeting 2026-11-11 09:58`, in hybrid mode with a limit of 8 (the agent-activity log records the call) and got eight unrelated chunks from another project.

The collection held 384,885 memories. The query's terms `meeting`, `2026`, `11`, `09` and `58` matched 1,180, 34,395, 34,860, 18,735 and 11,300 of them. ADR 0005's lexical channel read the first 256 points holding *any* term; Qdrant returns them in point-id order, so the 256 were arbitrary points holding `2026` or `11`, and the meeting was not among them, although its lexical score was 11.65 against 4.73 for the best chunk returned. The semantic channel did not hold it among its 256 either: a 24-character title is far from 6,000 characters of Romanian conversation. ADR 0005's invalidation condition had been met: the corpus had grown until the 256-candidate cap measurably dropped a lexical hit.

Fixing the candidates alone was not enough. With the meeting read, it ranked 25th: reciprocal rank fusion gives a row that is first in one list 1/61 = 0.0164, any row within about 60 places of the top of both lists scores more, and the eighth result scored 0.0199.

## Decision

The lexical channel still reads at most 256 points, now in this order:

1. When the query has several terms, the points holding every one of them.
2. While room is left, each term's points, the rarest term first, by an exact count within the search's filters.

When a query of two or more terms appears verbatim in a candidate's content or source, ignoring case and runs of whitespace, the candidate also ranks in a third list, ordered as the lexical list is, and reciprocal rank fusion sums all three. A single-term query has no third list: the lexical list already is that term. Semantic mode, the filters, the match types and the scores the results expose keep their meaning. MCP `search`, `recall` and the dashboard reach this through the same `vectorstore.search`.

## Rejected alternatives

- **Fixing the candidates without the third list.** Measured on the same collection: the meeting is found but ranks 25th, outside the eight results the agent asked for.
- **A larger cap.** The common terms matched up to 34,860 points here, so any cap short of that still leaves which points are read to point-id order, while every read point costs a payload transfer.
- **A third list of points holding every term, instead of verbatim matches.** After stop words, the terms of an ordinary question often all appear in loosely related passages, so it would reorder ordinary questions as well as titles. Not measured.
- **Verbatim matches first, outright.** A short common phrase (`error handling`) appears in many chunks, and every one of them would rank above the best semantic answer. Not measured.

## Consequences

- An exact title, quoted phrase or identifier sequence is found and ranked in a large corpus: on the 384,885-memory collection the meeting's title query returns it first (score 0.0328), where before it was absent from the first 50.
- Each hybrid search with several terms makes one more scroll and one exact count per term (12–16 ms each over loopback HTTP on that collection), then one scroll per term until the 256 fill. Measured end to end, search took the same time before and after (about 2.1 s and 1.2 s for the two queries above, dominated by other stages).
- A rare term is read now, but a point that only matches terms, without the phrase and without semantic support, still ranks by the lexical list alone: `meeting transcript notes 11 November 2026 ședință` finds the meeting 29th (absent from the first 50 before).
- Rankings change for queries of several terms that appear verbatim in some memories: those memories rise. None of the 46 retrieval-benchmark queries appears verbatim in the benchmark corpus, so the third list leaves those results as they were.
- This is still not BM25 or a global ranking. When every term is common and the points holding all of them exceed 256, which of them are read still follows point-id order.

## Invalidation conditions

- If Qdrant offers ranked full-text retrieval (BM25 or sparse vectors over the payload) through its server API, replace both the candidate order and the third list with it, after the held-out evaluation ADR 0005 asks for.
- If the per-term counts take a measurable share of search time on a production-sized collection (more than a tenth of a search), count approximately, in parallel, or for fewer terms.
- If verbatim matches are shown to push better semantic answers out of the first results for ordinary questions (a benchmark result or a reported case), weight the third list lower or limit it to queries shaped like titles and identifiers.

## Validation

- Windows 11, Python 3.13 (the installed runtime's venv), from a native copy: `tests/test_indexing_retrieval.py` adds a storage test (30 memories holding the common terms, a candidate cap of 8, the meeting last by point id and semantically unrelated) and a fusion test; both fail on the previous code and pass with this change. The full suite passes there.
- Live and read-only, on the 384,885-memory collection: the title query ranks the meeting first, against absent from the first 50 with the installed code; the timings above come from the same runs.
- Not run: the retrieval benchmark harness (it needs its isolated snapshot, Qdrant and model setup, and its runner uses the POSIX-only `resource` module). Only the verbatim check of its 46 queries against its corpus was made, so the effect of the new candidate order on its scores is unmeasured. Linux and macOS were not run locally; CI covers them.
