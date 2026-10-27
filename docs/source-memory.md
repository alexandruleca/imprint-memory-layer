---
title: Verifiable source memory
description: What full-source snapshots preserve, how to read and verify them, how they are removed and what the paired coverage experiment establishes.
---

New file ingestion, conversation learning, URL ingestion and replacement of inline content store both searchable chunks and a complete redacted readable source. The source is compressed in `data/source-snapshots.sqlite3`, scoped by workspace, project and source key. [ADR 0081](adr/0081-verifiable-redacted-source-snapshots.md) and [ADR 0082](adr/0082-retain-readable-conversation-evidence.md) describe the decisions.

## Read and verify

Search returns bounded excerpts. `get_memory` expands one chunk and includes span metadata and a `source_read` pointer. `file_chunks` pages through chunks with overlap and filename prefixes. For complete text, call `read_source(source, project, start=0, max_chars=4000)`. Continue from `next_start` with the returned `version`; an intervening replacement fails the continuation rather than mixing versions. Pages are 100–12,000 characters. `verify=True` compares all expected chunk IDs, content hashes, spans and source versions with the index, reporting missing, changed and extra chunks. The snapshot remains readable if the index is incomplete.

This tool reads stored text only. It never opens a path from a memory, including a foreign device's path. A version identifies the current head; older versions are not retained. Snapshot publication is atomic within SQLite and occurs after vector writes and cleanup succeed. The two stores do not share a transaction: readers may observe a mismatch during replacement, or after interruption. The next learning/refresh repairs it; the integrity result exposes it.

## What survives

UTF-8 source files retain their complete redacted text, including CRLF, whitespace and files shorter than 100 characters. Derived filename prefixes and overlapping slices can be removed using the manifest to rebuild that text exactly. Extracted documents and URLs retain extracted readable text, not their original binary/HTML container. Inline append mode (`replace=False`) and individual `store` notes have no full-source snapshot.

Conversation snapshots include readable user and assistant messages, brief acknowledgements, available analysis/reasoning summaries and rationale, tool arguments/results and readable attachments. Events retain role, kind, turn and available timestamps/call IDs. Client context has its own role. Unknown event formats may still require a reader update. Encrypted/opaque reasoning and binary attachments are omitted and counted where recognized; no unavailable thinking is inferred or decrypted. A transcript line that a crash left as NUL bytes is skipped and counted (`zero_filled_lines`), and a record appended after the NULs is still read; any other line that is not JSON fails the reading ([ADR 0088](adr/0088-bytes-a-crash-never-wrote-are-counted-omissions.md)). Findings and decisions are evidence of what the model observed or concluded, not automatically established facts. Retrieved source text remains untrusted reference material.

Redaction runs on the whole readable source before chunking, so a credential crossing a future cut is redacted as one value. Current redaction also applies to export, import and `scrub`; stricter stored redactions propagate to the full source. The deterministic detector is not comprehensive secret detection. Snapshot text is compressed, not encrypted at rest, as with local memory storage; its SQLite file has mode 0600 on POSIX.

## Lifecycle and upgrade

- Conversations are learned again once under learner version 3. More readable evidence means more embedding work; existing embeddings are reused where content and position agree. Tool-only/context-only chunks do not create an LLM tagging backlog by themselves.
- Existing unchanged file/URL records remain readable as legacy chunks. Run `imprint refresh <directory> --force` or URL ingestion with `--force` to backfill snapshots. Modern files with a changed source profile or incomplete manifest are refreshed automatically.
- Purge, source deletion, missing-file pruning, workspace deletion and wipe remove the corresponding snapshots. Explicit deletion of one memory invalidates the entire source snapshot, so the deleted text cannot still be read through that archive. Remaining chunks then have no authoritative full-source view until a permitted reingestion. Purge exclusions remain the durable mechanism for preventing reintroduction.
- Project moves rebind manifest IDs. Workspace migration carries a full source only when all its expected chunks are present in the destination. A partial topic copy cannot expose the unselected remainder through `read_source`.
- Live encrypted sync and local snapshot bundles carry compressed, checksummed source frames, plus span metadata on chunks. Incomplete frames never publish a source. Heads use source update time to keep the newer snapshot; vector sync still merges points without deletion propagation, so old peer chunks may appear as extras. Cross-device clock skew can choose the wrong head. Older clients ignore the additional live frames and cannot provide source reconstruction. Mixed-version end-to-end interoperability is unverified.

Snapshots have a 64 MiB readable-text limit and at most 100,000 manifest entries. Transfers and decompression are bounded. File admission limits still apply first. This is one current source head, not backup history. No startup migration rewrites the production index.

## Evidence and limits

The [paired experiment](../benchmark/source-memory/README.md) on native macOS arm64 compares dev `1e8eb53` with this implementation, using disposable storage and the same Qwen embedding model. Twelve fixed synthetic queries require the returned chunk to contain the actual supporting evidence. Evidence Recall@1 and Recall@5 rose from **4/12 to 12/12**, because previously omitted content became indexable. All four controls already retrievable before remain first-ranked.

The same run reconstructed all **171 readable conversation representations and 594 supported repository code/text files** exactly from their spans. This does not mean every historical JSON field was ingested, or prove the old index had large byte gaps. The earlier device audit found all 1,561 expected old chunks in 169 up-to-date conversations; the omissions were mainly in reading transcripts before chunking.

The cost is visible: local conversations grew from **1,580 to 3,586 chunks** and **2.65 to 19.12 million indexed characters**, with **3.88 MB** of compressed source bodies. Repository files used **2,295 to 1,562 chunks**, with **1.74 MB** of compressed source bodies. SQLite/manifests/vectors add further overhead. Local preparation plus synthetic cases took 9.4 s before and 15.2 s after; these single-run timings are order/cache dependent. Only the synthetic corpus was embedded during the experiment. General relevance, answer correctness, large-corpus latency and actual full-corpus embedding time remain unmeasured. Windows/Linux runtime, installers and real paired-device end-to-end behavior remain unverified for this change.
