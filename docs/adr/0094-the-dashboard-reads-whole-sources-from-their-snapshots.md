---
title: ADR 0094 — The dashboard reads whole sources from their snapshots
description: Topics, the graph, sources and search open a conversation or file whole, read from its stored redacted snapshot, with a topic's passages marked.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: the dashboard's Topics, Graph, Projects, Sources and Search pages and the API they read
- Related: [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (snapshots and `read_source`), [ADR 0082](0082-retain-readable-conversation-evidence.md) (what a conversation snapshot holds), [source memory](../source-memory.md)

## Context

The user asked to open a topic in the graph and read whole conversations, not
only individual chunks. The dashboard showed a topic as up to 200 chunk excerpts
(`build_topic_detail` scrolled 200 points and reported their count as the
topic's total) and named every conversation by its key, `conversation/1c7cd7f6`.

Every source stored since ADR 0081 has a full redacted snapshot. A copy of this
user's snapshot database held 117 conversation snapshots; each conversation
source listed for `imprint-memory-layer` (11) had one. A conversation snapshot is
the text `conversation_source.prepare` writes: an intro line, then one
`--- {json} ---` header per event (event number, role, kind, turn, timestamp,
tool, call ID) followed by the event's text. Topic tags are on chunks, and each
chunk's span in the snapshot is in the snapshot's manifest.

## Decision

The dashboard reads whole sources from their snapshots and nothing else.

- `GET /api/sources/read?source=&project=&topic=` returns a source's snapshot in
  blocks. A conversation's blocks are its events, read back from the header
  lines by `conversation_source.read`, the inverse of `prepare`'s rendering; a
  header counts only when it carries the next event number. A file's blocks are
  its text cut where its chunks end. Each block lists the chunks that cover it
  and is marked `match` when one of them carries `topic`. The server slices the
  text; the browser never applies offsets.
- `GET /api/topic/{name}` returns every memory tagged with the topic, grouped by
  project and source, largest group first; `?project=` limits it.
- A conversation is named by its first typed question
  (`conversation_titles`): the first line of the first user message that holds
  words outside tags a client wrapped around an attachment. Titles are read from
  the first 16 KiB of each compressed body and cached until the snapshot
  database changes. Source lists, topic groups and graph source nodes use them.
- The reader shows a conversation as its exchange: messages in order, the tool
  calls (paired with their output), thinking and injected context between two
  messages folded into one row. Neighbouring marked items form one passage,
  and the reader steps through passages.
- A source without a snapshot is not reconstructed: the reader says how to keep
  one (learn the conversation again, or refresh the file with `--force`).

## Rejected alternatives

- **Rebuild a conversation from its chunks** (`file_chunks` order). Chunks
  overlap and carry retrieval prefixes, and ADR 0081 holds that legacy chunks
  cannot prove reconstruction; snapshots already hold the exact text.
- **Read the transcript file again.** Memories never open their source paths,
  and a source recorded on another device has no local file.
- **Store each event's offsets in the snapshot metadata at learn time.** It
  changes the stored format and needs every conversation learned again, while
  the header lines already carry the same metadata: reading a 3.9 MB,
  3,037-event snapshot back took 16 ms, and the titles of all 117 conversations
  took 47 ms, in Python 3.12 on WSL.
- **Send the text with offsets and slice it in the browser.** Snapshot offsets
  count Unicode code points; JavaScript strings index UTF-16 units, so any
  character outside the Basic Multilingual Plane would shift every later slice.
- **Mark single events for a topic.** Tags exist per chunk only; marking finer
  than the chunks that carry the tag would claim more than the store knows.

## Consequences

- Topics list conversations and files by name with their tagged passages, and
  every list of sources opens one whole. Search results and graph chunks open
  their source at their own chunk.
- `conversation_source.read` must follow any change to `prepare`'s rendering;
  `tests/test_source_reader.py` checks that reading back gives `prepare`'s
  events and texts.
- Marks are as coarse as chunks: a chunk of about 5,600 characters covers
  several events, and one folded activity row can span several chunks, so
  neighbouring marks merge into longer passages.
- A response carries a whole snapshot (3.9 MB for the largest measured
  conversation) over the loopback API. A topic's detail now reads all its
  memories, with 300-character excerpts.

## Invalidation conditions

- If the conversation snapshot format changes (a new `source_memory`
  format version or a different event rendering), update
  `conversation_source.read` with it, or store event spans in the snapshot.
- If tags are recorded per event rather than per chunk, mark events directly.
- If sources of tens of megabytes become common, page the reader the way
  `read_source` pages, pinned to the snapshot's version.
- If titles from the first question prove unreliable (most conversations
  starting with attachments), store a title at learn time instead.

## Validation

- `tests/test_source_reader.py`: events read back as prepared, with a quoted
  header kept as text; a cut-off start; titles that skip attachment tags; a topic
  grouped by source within and across projects; every block's covering chunks
  against a brute-force overlap; a file reassembled from its blocks; the 404 for
  a source without a snapshot.
- `imprint/ui/tests/source-reader.spec.ts`: a topic's sources by title, the
  project filter, the reader's passages and folded tool calls, the missing
  snapshot message, and the graph's topic opening the reader.
- Titles and parsing were run against a copy of this user's snapshot database
  (117 conversations). The reader was screenshotted with a real 54-event
  conversation. Not verified: the dashboard against a live native Windows or
  macOS runtime, and the Electron window.
