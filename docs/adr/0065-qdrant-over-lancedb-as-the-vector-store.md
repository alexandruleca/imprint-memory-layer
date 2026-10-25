---
title: ADR 0065 — Qdrant over LanceDB as the vector store
description: Imprint stores memories in Qdrant, which replaced LanceDB in April 2026; the owner recalls choosing it because Qdrant runs as a server, locally or remotely, and LanceDB's open-source edition does not.
---

- Status: Accepted
- Date: 2026-04-13
- Recorded: 2026-10-25
- Retrospective: reconstructed from git history and the owner's recollection: commit b4d504d, which replaced the LanceDB store with Qdrant and added `migrate_lance_import.py`; the LanceDB store it replaced (`knowledgebase/vectorstore.py` and `requirements.txt` at 7068585); and the owner's answer on 2026-10-25. The feature comparison is from the vendors' documentation and package registries as of 2026-10-25, not from the time of the decision
- Related: [ADR 0063](0063-auto-spawned-local-qdrant-server.md) (how Qdrant runs: one auto-spawned local server for every process), [ADR 0024](0024-enforce-local-service-boundaries.md) (the managed server is loopback-only; a separately run server goes through bring-your-own mode), [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) (hybrid retrieval over the store)

## Context

Until b4d504d (authored 2026-04-13, "replaced db with qdrant"), Imprint stored memories in LanceDB. `requirements.txt` required `lancedb>=0.20.0`, and `knowledgebase/vectorstore.py` opened `data/lance` with `lancedb.connect`, kept one table whose tags were a comma-separated string, searched it with SQL `where` filters, and called `compact_files()` every 200 inserts because "after thousands of small batches the table holds many tiny files that pyarrow buffers in memory on every scan."

b4d504d replaced that store with Qdrant v1.17.1. The same commit added structured tag payloads with payload indexes, the auto-spawned local server (ADR 0063), and `migrate_lance_import.py`, which re-ingests a LanceDB JSONL dump; the script is still in the repository. From that first commit the client offered a server Imprint does not start: "To use a managed/remote server instead, set KNOWLEDGE_QDRANT_HOST and KNOWLEDGE_QDRANT_NO_SPAWN=1 to skip the auto-spawn."

The commit records no reason for the switch, and no document, issue or benchmark from the time compares the two stores. Asked on 2026-10-25, the owner answered: "I believe because it can run remote and lancedb no, we should compare the feature sets." This record takes that as the reason, as the owner recalls it; it is not otherwise proven. The bring-your-own-server option b4d504d shipped is consistent with it.

Which LanceDB version Imprint ran is not recorded; the newest release on the decision date was 0.30.2 (2026-03-31).

## Decision

Imprint's vector store is Qdrant, used through its server API (`qdrant-client` over HTTP), so the same storage code serves the auto-spawned local server (ADR 0063) and a server run separately, on the same machine or another (`qdrant.no_spawn`). LanceDB is not used.

## Rejected alternatives

- **Keeping LanceDB.** The owner recalls that LanceDB could not run as a remote server and Qdrant could. No other evaluation from the time was recorded. Today's documentation confirms that distinction for the open-source editions (see the comparison below): LanceDB OSS "runs inside your application process", and its server form, LanceDB Enterprise, is a commercial product, while Qdrant's server is open source.

## Comparison as of 2026-10-25

This comparison was made when the decision was recorded, to check the owner's recollection against the features Imprint uses. It is not the evidence the decision was made on, which beyond the owner's recollection is unknown. Versions: Qdrant server v1.20.0 (released 2026-10-22; Imprint pins v1.17.1) and `qdrant-client` 1.20.0; LanceDB 0.40.0 for Python (released 2026-10-07).

| Need | Qdrant | LanceDB |
| --- | --- | --- |
| Server and remote deployment | Client–server. The open-source server runs as a binary, container or Kubernetes deployment, on one node or as a distributed cluster (cluster mode, since v0.8.0). The vendor also sells hosting: Qdrant Cloud (a managed service with free and paid clusters), Hybrid Cloud and Private Cloud. The installation page recommends Kubernetes or Qdrant Cloud for production, allows Docker there, and offers the binary only "for testing or development setups". | LanceDB OSS is an embedded library: "Single process", "Embedded in your code". Its data can live on local disk or in object storage (S3, GCS, Azure Blob), but queries run in the application process. LanceDB Enterprise, the server form (managed or bring-your-own-cloud cluster, reached through `db://` connections), is commercial. |
| Several processes on one store | Any number of clients share one server. The embedded options run inside one process: the Python client's local mode locks its folder ("If you require concurrent access, use Qdrant server instead") and is meant for "development, prototyping and testing"; Qdrant Edge (beta) is an in-process library whose documentation does not describe several processes sharing a shard. | Several processes can open one table without a server and without a lock: "For writes, we support concurrent writing, though too many concurrent writers can lead to failing writes as there is a limited number of times a writer retries a commit." By default a reader does not check for other processes' writes; `read_consistency_interval` sets how often it does. |
| Full-text and hybrid search | Full-text payload index (word, whitespace, prefix and multilingual tokenizers; code tokenizer since v1.20.0; optional stemming, stopwords and phrase matching), used for filtering. BM25 ranking from that index since v1.20.0; sparse vectors, with server-side `qdrant/bm25` embedding; Query API fusion with RRF (v1.10.0) and DBSF (v1.11.0). Imprint's pinned v1.17.1 predates BM25 on the text index: Imprint filters candidates with it and fuses the rankings itself (ADR 0005). | BM25 full-text index (`create_fts_index`) and hybrid search that combines vector and full-text results through a reranker, RRF among them. In OSS, rows added after an index was built are searched exhaustively until `optimize()` adds them. |
| Metadata filtering and indexes | Payload indexes on keyword, integer, float, bool, geo, datetime, text and uuid fields, kept on disk instead of in RAM on request since v1.11.0; the filterable HNSW index adds edges for indexed payload values. Imprint indexes 13 fields. | SQL `where` filters, applied before (default) or after the vector search. Scalar indexes: BTREE, BITMAP, LABEL_LIST and FM. The docs advise IVF_RQ or IVF_PQ for filtered workloads, because the HNSW-based indexes "can show higher latency variance" with filters. |
| Quantization | Scalar int8 (v1.1.0), binary at 1, 1.5 or 2 bits (v1.5.0; 1.5 and 2 bits v1.15.0), product (v1.2.0), TurboQuant (v1.18.0) and asymmetric (v1.15.0), with optional rescoring against the original vectors. Imprint keeps int8 vectors in RAM and the originals on disk. | Part of the vector index: product (PQ), RaBitQ (RQ, 1, 2, 4 or 8 bits) and scalar (SQ, in IVF_HNSW_SQ). |
| Approximate nearest-neighbour index | "Qdrant currently only uses HNSW as a dense vector index." The server's optimizer builds it in the background; small segments are scanned in full. | IVF_PQ, IVF_RQ, IVF_HNSW_SQ, IVF_HNSW_PQ and IVF_HNSW_FLAT; HNSW only inside IVF partitions; exact scan without an index. In OSS, indexing and compaction are manual (`create_index`, `optimize()`). |
| Snapshots and backup | Collection snapshots as `tar` archives, created and restored through the API (since v0.8.4). Imprint keeps them in `data/qdrant_snapshots/`. | Data changes create table versions; earlier versions can be checked out and restored, and tagged versions survive cleanup, which `optimize()` runs for versions older than 7 days by default. The documentation index has no backup page. |
| Python and Go clients | Official clients for Python, JavaScript/TypeScript, Rust, Go, .NET and Java, over REST and gRPC. | Official SDKs for Python, TypeScript and Rust; a REST-based Java SDK for Enterprise only; Go is a community SDK (`lancedb-go`, v0.1.2 from 2025-09-30). |
| Windows, macOS and Linux | Release binaries for Linux x86-64 (glibc and musl) and ARM64 (musl), macOS x86-64 and ARM64, and Windows x86-64. 64-bit only; storage must be block storage with a POSIX-compatible file system, not NFS or object storage. | 0.40.0 wheels for Windows x86-64, Linux x86-64 and ARM64 (glibc 2.28+) and macOS ARM64. There is no macOS x86-64 wheel (0.20.0 had one); Imprint supports macOS Intel. |
| License | Server and clients Apache 2.0; the hosted offerings are separate services. | LanceDB OSS Apache 2.0; Enterprise is commercial. |

What this shows for Imprint:

- The owner's recollection holds for the open-source editions today: Qdrant's open-source server runs locally or on another machine, and LanceDB has no open-source server. LanceDB OSS can keep its data in remote object storage, but each application process still runs the queries.
- Today's LanceDB OSS lets several processes read and write one table without a server, retrying conflicting commits. ADR 0063's daemon answers a limit of Qdrant's embedded mode that LanceDB does not have. Whether this was weighed in April 2026 is not recorded.
- The features Imprint relies on, keyword and text payload indexes on 13 fields, int8 quantization, and indexing that keeps up with incremental writes without scheduled maintenance, are all in Qdrant's server. With LanceDB OSS, Imprint would schedule `optimize()` itself, as its LanceDB store already compacted fragments by hand, and today's release would not install on macOS Intel.

Sources, fetched 2026-10-25. Qdrant: [installation](https://qdrant.tech/documentation/installation/), [distributed deployment](https://qdrant.tech/documentation/scaling/distributed_deployment/), [indexing](https://qdrant.tech/documentation/manage-data/indexing/), [quantization](https://qdrant.tech/documentation/manage-data/quantization/), [hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/), [full-text search](https://qdrant.tech/documentation/search/text-search/full-text-search/), [BM25 inference](https://qdrant.tech/documentation/inference/inference-bm25/), [snapshots](https://qdrant.tech/documentation/snapshots/), [client libraries](https://qdrant.tech/documentation/interfaces/), [Qdrant Edge](https://qdrant.tech/documentation/edge/) and [Edge compared with the server](https://qdrant.tech/documentation/edge/edge-vs-qdrant-cluster/), the [Python client README](https://github.com/qdrant/qdrant-client) (local mode; the lock message is in `qdrant_client/local/qdrant_local.py`), and the [v1.20.0 release](https://github.com/qdrant/qdrant/releases/tag/v1.20.0) assets. LanceDB: [Enterprise compared with OSS](https://docs.lancedb.com/enterprise), [OSS FAQ](https://docs.lancedb.com/faq/faq-oss), [consistency](https://docs.lancedb.com/tables/consistency), [storage](https://docs.lancedb.com/storage), [full-text index](https://docs.lancedb.com/indexing/fts-index), [hybrid search](https://docs.lancedb.com/search/hybrid-search), [filtering](https://docs.lancedb.com/search/filtering), [scalar indexes](https://docs.lancedb.com/indexing/scalar-index), [vector indexes](https://docs.lancedb.com/indexing/vector-index), [quantization](https://docs.lancedb.com/indexing/quantization), [reindexing](https://docs.lancedb.com/indexing/reindexing), [versioning](https://docs.lancedb.com/tables/versioning), [SDKs](https://docs.lancedb.com/api-reference) and the [0.40.0 package](https://pypi.org/project/lancedb/0.40.0/). Licenses and release dates are from the GitHub and PyPI metadata of each project.

## Consequences

- Imprint can use a Qdrant server it does not start, on the same machine or another, through `qdrant.no_spawn`; the operator secures that server (ADR 0024).
- Every Imprint process reaches the store through a server, which ADR 0063 runs as a local daemon; ADR 0063 records its measured cost.
- LanceDB data did not carry over: it had to be dumped to JSONL and re-ingested with `migrate_lance_import.py`, which also converted tags to structured payloads.
- The storage code is written against Qdrant's API (`imprint/vectorstore.py`: filters, payload indexes, scrolls, collection metadata). Changing stores again means rewriting that module and exporting or re-ingesting every collection.
- Qdrant's installation page offers the binary only for testing or development and recommends Kubernetes or Qdrant Cloud for production. Imprint runs that binary as a single-user local service.

## Invalidation conditions

- If Imprint drops support for a separately run or remote server (`qdrant.no_spawn`), the recalled reason is gone: reopen the choice, weighing an embedded store (LanceDB OSS, Qdrant Edge) against ADR 0063's daemon and its measured cost.
- If LanceDB publishes an open-source server, the recalled reason no longer separates the two: reopen with this comparison refreshed.
- If Imprint needs to keep memories in object storage (S3, GCS, Azure Blob), which Qdrant does not support and LanceDB does, reopen the choice.
- If Qdrant's server stops being released under a permissive open-source license, reopen the choice.

## Validation

No validation was recorded with the decision; the repository holds no comparison or benchmark of the two stores from April 2026, and the owner's reason is recalled, not documented.

The comparison above was assembled on 2026-10-25 from the documentation and registry metadata it cites. No LanceDB code was run for this record, and no performance or memory comparison between the stores was made. The Qdrant server's own checks are listed in ADR 0063.
