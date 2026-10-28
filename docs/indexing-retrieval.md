---
title: Indexing and retrieval
description: Source lifecycle, freshness, ranking, migration, evaluation and known limitations.
---

## Source lifecycle

Full snapshot publication also suggests project associations. Accepted links
extend project read filters to `related_projects` without changing primary IDs
or copying vectors. Every writer, including sync, recomputes that payload from
device-local workspace decisions. Manual decisions survive refresh; inferred
links are reconsidered on source changes. Moves and purge retain primary-owner
semantics. See [project organization](workspaces.md#associate-one-source-with-several-projects)
and [ADR 0099](adr/0099-sources-can-belong-to-several-projects.md).

Filesystem ingestion is restricted to approved workspace roots; overlapping roots require explicit sharing confirmation. Purged files/directories are excluded from refresh until restored, and storage/import checks reject their reintroduction. Workspace selection remains fixed through source replacement even when another window switches the active workspace. See [workspace roots and purge](workspaces.md) and ADR 0015.

`imprint ingest <dir>` and `imprint refresh <dir>` use the same pipeline. Scan selected roots, respect git tracked/ignore boundaries (the walk enters only directories holding a file git lists, so ignored trees are never read), filter generated/secret filenames and follow no links: the walk lists no symbolic link or Windows junction, so every file it lists lies under its root without resolving a path ([ADR 0110](adr/0110-the-scan-follows-no-links.md)). Git filenames use NUL delimiters. Inside a repository the file set is git's listing: a repository in a WSL distribution, scanned by a Windows runtime, is listed by the distribution's git, and a repository git cannot list (git missing, refusing or timing out) stops `ingest`/`refresh` with a one-line reason instead of being walked without its ignore rules ([ADR 0107](adr/0107-git-lists-a-repositorys-files-wsl-repositories-through-their-distribution.md)). Non-git walking propagates scan errors. Known secret names are a conservative exclusion. Inside indexed content, credentials in known formats are replaced with placeholders before storage ([secret redaction](redaction.md)); review selected sources before indexing all the same.

Fingerprint bytes with SHA256 and store `source_hash`, `source_size`, `source_path` and `source_mtime` on local file chunks. Compare fingerprints rather than mtime alone, including timestamp-preserving edits. Check again after extraction. Text files are UTF-8; undecodable input fails visibly and retains prior chunks. Source text defaults to 2,000,000 bytes (`imprint config set ingest.max_file_bytes 4000000` changes it). Documents have the separate `ingest.max_doc_size_mb` extraction policy.

Missing-source pruning resolves both recorded paths and requested roots before comparing boundaries. Equivalent macOS `/var` and `/private/var` spellings do not prevent cleanup; a symlink resolving outside a requested root is still excluded.

Prepare every chunk for a source before replacement. Upsert first; delete obsolete IDs only after successful embeddings/writes, matching project and source. Independent sources can succeed even if another file fails. Verified empty text removes prior chunks. Failed reads, decoding, oversize files, unavailable extractors or extraction errors retain the prior index and make the run incomplete/nonzero. A document whose extractors return no text, such as a photo without text or a scanned PDF without OCR, is skipped with a note and does not make ingestion incomplete. TypeScript and JavaScript configuration files (`tsconfig*.json`, `jsconfig*.json`) and `.jsonc` files are ingested as verbatim code text, preserving comments and trailing commas; other JSON documents retain JSON/JSONC parsing and structured export detection, with malformed input reported as an error. The run lists it with the reason and the OCR setting that could read it, and keeps the chunks of an earlier version if it had one. The result is stored per content hash and extraction profile (OCR settings and tools) in `data/extraction-results.sqlite3`, so refresh reads the document again only after the file or the profile changes, or with `--force`. See [ADR 0040](adr/0040-documents-with-nothing-to-index.md). Storage failures propagate. Removed files are pruned only when their recorded absolute path is actually missing inside a successfully scanned requested root. Excluded-but-existing files are not silently pruned; legacy chunks of a newly excluded secret may need explicit deletion.

Replacement is not a database transaction. A reader may briefly see old and new chunks, and an interrupted upsert may leave additional chunks until retry. Fingerprint inconsistencies force reprocessing. Deletion history is not a backup. Last-known source state is derived from stored chunks, so empty text files are revisited on later runs. Sources with no provenance are retained until reindexed or explicitly removed.

The public ID hashes complete stripped content, project, source and optional chunk position. The project is the one the workspace's placement rules give the record's origin project, applied at every write before the ID is computed ([ADR 0036](adr/0036-move-memories-between-projects.md)); moving a memory gives it the ID it would have under its new project. Different long chunks sharing a prefix and identical text at separate positions remain distinct. Single memories have no position component. Existing logical IDs/point UUIDs remain readable. Source refresh replaces legacy chunk IDs without a bulk migration; independently re-saving an old note may create a new identity. Dedup checks retrieve the requested IDs from storage rather than trusting a process-local cache that another process can invalidate.

`--force` on ingest/refresh bypasses both fingerprint skipping and existing-vector dedup within a compatible profile. After changing embedding identity, rebuild in a fresh workspace; see [embeddings](embeddings.md). Indexing failures propagate nonzero through the Go CLI.

Complete redacted readable text and span manifests are stored beside modern chunks, after successful replacement. `read_source` provides version-pinned pages and detects incomplete index coverage. Modern skip checks also verify the source profile and manifest ID set; missing chunks trigger repair. Legacy files need `refresh --force` once to acquire a full-source view. See [source memory](source-memory.md) for content boundaries, deletion, sync and measured costs ([ADR 0081](adr/0081-verifiable-redacted-source-snapshots.md)).

## Audio sources

Audio recordings use this same source lifecycle, with their own size/duration
limits. The multilingual CPU recognizer emits timestamped neutral-speaker
conversation events. Completed source snapshots cache transcripts by file hash
and transcription profile, inside the workspace; `--force` retranscribes.
See [audio ingestion](audio-ingestion.md) and [ADR 0095](adr/0095-audio-files-enter-the-source-ingestion-pipeline.md).

## Ranking contract

MCP and dashboard call the same vectorstore search. Default `hybrid` uses the existing semantic vector, Qdrant full-text candidates and deterministic reciprocal rank fusion. Lexical scoring considers term coverage/frequency and exact identifier, path and phrase signals. `semantic` remains available for comparison. Filters apply before ranking. At most 256 candidates per channel are read, query terms cap at 12, pages at 50, and offsets at 255.

Results include semantic `similarity`, `match_type`, `lexical_score` and `retrieval_score`, plus available provenance. These scores have different meanings; none is a probability that an answer is correct. A lexical-only candidate can have semantic similarity zero. Hybrid search still requires embeddings and is not a lexical-only fallback when models fail. Qdrant's bounded lexical scroll is not global BM25 and can miss relevant candidates in large corpora. Pagination is not a snapshot during concurrent writes.

Search results are untrusted evidence. Source hashes describe indexed bytes; retrieval does not read and verify the live filesystem automatically. Check current files before editing. URL cache freshness relies on server-provided ETag/Last-Modified; it is not cryptographic provenance. Re-sending an inline source/URL replaces only its project/source set after successful preparation.

## Evaluation to add next

The [2026-10-23 public benchmark](benchmarks.md) now supplies a first frozen
source-retrieval baseline: 42 positive cases and four negative/boundary cases,
with five timing repetitions per mode. Hybrid Recall@5 is 0.833 versus semantic
0.802; MRR@10 is 0.839 versus 0.869. The paired query-family interval for the
Recall@5 difference includes zero. All 60 explicit filter/boundary checks pass.
This is a small, task-aligned public corpus, not a held-out customer or
multilingual evaluation. Ranking weights were not tuned against these results.

The regression suite proves specific invariants, not overall retrieval quality. Build a versioned held-out set before tuning weights or adding another model:

| Case | Required behavior | Measure |
| --- | --- | --- |
| Exact identifier/decision | Known source appears near the top | Recall@5/10, MRR |
| Paraphrased behavior | Find related code without exact words | Recall, nDCG |
| Same terms across projects/workspaces | Filters isolate the requested scope | Leakage rate must be zero |
| Missing fact | Assistant reports insufficient evidence | Unsupported-answer rate |
| Changed/deleted/renamed source | Refresh retires obsolete chunks and exposes provenance | Freshness/deletion correctness |
| Repeated headers/generated boilerplate | Useful chunks are not crowded out | Duplicate rate, diversity |
| Remote/model/storage failure | Preserve prior data and expose failure | False-success count must be zero |
| Large/multilingual corpus | Bound latency/memory while retaining relevance | p50/p95, peak RAM, recall |

Compare semantic baseline and hybrid on the same corpus with blinded relevance labels. Track answer correctness and source citations in addition to tokens. Then evaluate BM25, a small reranker, syntax-aware symbol metadata, result diversity and a generation-based atomic replacement design. Add opt-in watching only after deletion/ignore/resource controls have production evidence.

See [ADR 0005](adr/0005-source-lifecycle-and-hybrid-retrieval.md) and [platform validation](platform-validation.md).
