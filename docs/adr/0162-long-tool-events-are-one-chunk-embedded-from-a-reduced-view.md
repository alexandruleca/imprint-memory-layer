---
title: ADR 0162 — Long tool events are one chunk, embedded from a reduced view
description: In a learned conversation, a tool output or tool call longer than the largest ordinary chunk is stored as one chunk holding its exact text, and its vector is computed from the event's opening lines, error lines and closing lines instead of from a run of overlapping chunks.
---

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0082](0082-retain-readable-conversation-evidence.md) — how long tool evidence is divided into search chunks and what their vectors are computed from
- Related: [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (snapshots and covering spans, unchanged), [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md) (learner version, unchanged), [ADR 0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) (bounded excerpts), [source memory](../source-memory.md)

## Context

[Headroom](https://github.com/headroomlabs-ai/headroom) compresses agents' tool outputs (logs, JSON, search results) by keeping errors, boundaries and unusual lines and dropping repetition. Imprint's own MCP output is already bounded, query-focused excerpts with an expansion hint (ADR 0021), so the question was where Imprint stores tool output itself: learned conversations (ADR 0082).

Measured on the owner's index (Windows install, 452 learned conversations, aggregate counts only), conversation snapshots hold 120.4 million characters: tool outputs 44.0%, tool calls 34.4%, user and assistant messages 8.3%, analysis 1.5%. They make 24,189 chunks, 60% of which are at least 80% tool calls or outputs. Tool outputs have a median of 512 characters, a 99th percentile of 17,009 and a maximum of 776,919; the 1,179 longer than 8,000 characters hold 16.5% of all text. Such an output was cut into overlapping 6,000-character chunks, each embedded and each a separate search hit, though most of a build log or file dump repeats itself.

## Decision

1. **One chunk per long tool event.** When a learned conversation is chunked, a `tool_output` or `tool_call` event whose span (its header line through its closing newline) is longer than `chunker.hard_max` becomes one chunk. An event longer than 262,144 characters is cut at line ends into pieces no longer than that. The text between long events is chunked as before. Chunks still cover the snapshot exactly and each chunk's content is its exact span, so reconstruction, `read_source` verification, sync and workspace migration are unchanged.
2. **Its vector comes from a reduced view.** A chunk longer than `chunker.hard_max` that starts with a tool event header is embedded from: the header; the opening lines (two fifths of `chunker.size_prose`); lines between them that report an error, failure, warning, timeout, missing item or exit status, one per line shape; and the closing lines (one fifth). Runs of three or more lines that differ only in digits or hex identifiers are collapsed with a count, and a gap is marked with the number of omitted lines. The embedding entry points apply it, so learning, sync re-embedding, `scrub` and reindexing compute the same vector. Ordinary chunks never exceed `chunker.hard_max` and are embedded as before.
3. **No forced relearn.** The learner version stays 3: each conversation is chunked this way the next time it is learned (any change to it), and unchanged conversations keep their chunks.
4. `file_chunks` marks a chunk it cuts at 12,000 characters with a `get_memory` expansion hint.

## Rejected alternatives

- **Use Headroom as a library.** It brings a Rust-backed package, optional ML models and a telemetry beacon that is on by default. Its compressors rewrite the text an agent reads, while Imprint must keep the stored text exact; only the embedding input needs reducing, which takes a few line rules.
- **Leave the middle of long events out of the chunks.** A gap breaks the covering-span invariant that reconstruction, verification, sync and migration check (ADR 0081).
- **Store the reduced text as the chunk.** Keyword search and excerpts would lose the omitted lines, and the chunk would no longer be a span of the snapshot.
- **Reduce the tool content of every conversation chunk's embedding.** It would change the vectors of 60% of chunks, including the tool-only facts that ADR 0082's evidence cases retrieve, with no held-out measurement of the effect.
- **Bump the learner version.** Chunk indices after a conversation's first long event shift, so relearning re-embeds most of the index (about 104 million characters on the owner's).

## Consequences

Measured on the same index by re-chunking every snapshot: 24,189 chunks become 21,321 (11.9% fewer) and the characters embedded fall from 129.9 million to 104.4 million (19.7% fewer). 1,817 long-event chunks get a view with a median of 3,620 characters (largest 6,801) for events with a median of 11,256. All 452 snapshots rebuild exactly from the new chunks. A long log is one search hit (one per 262,144 characters) instead of one for about every 5,600 characters.

Dense search no longer reflects the middle of a long event, except lines that report a problem. Keyword search still matches all of it, and excerpts center on the matched terms. The lexical score normalizes only weakly for length, so one long chunk can rank higher than one of its former pieces did, though it now appears once. A long chunk's content is up to 262,144 characters in the payload and in sync; search excerpts and `get_memory` stay bounded. The index mixes both chunkings until conversations change. If the view's rules change later, vectors made with the old rules stay until their chunk is embedded again.

## Invalidation conditions

- If questions about lines in the middle of long tool outputs stop finding them (measured on stored queries or the evidence cases), then embed long events in several vectors or return to splitting them.
- If long chunks crowd keyword results, then add length normalization to the lexical score.
- If clients record tool events in a way the reading cannot attribute (no `tool_output`/`tool_call` kind), then they are chunked as before and this needs their adapter.
- If relearning the whole index becomes cheap (GPU embedding everywhere), then bump the learner version so old conversations are rechunked too.

## Validation

- `tests/test_long_events.py`: a long tool output is one chunk and the snapshot rebuilds exactly; transcripts without long tool events chunk as before; a long message chunks as before; an event longer than a piece is cut at lines without gaps; ordinary chunks embed unchanged; the view keeps the head, an error line from the middle and the tail within a chunk's size and collapses repeated lines; one huge line keeps its start and end; a long message event is not reduced; both embedding entry points use the view; learning a transcript stores the long output as one exact chunk.
- The aggregate measurements above, from a read-only copy of the owner's `source-snapshots.sqlite3`. No retrieval-quality run was made with the real embedding model.
