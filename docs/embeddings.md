---
title: Embeddings
description: Pinned Qwen3 embeddings, bounded local inference, model identity and rebuilding an index.
---

Imprint embeds code, documents, decisions and conversations locally. The default is [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), Apache-2.0, through the [ONNX Community export](https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX). The runtime uses 1024 dimensions, a 2048-token cap, CPU execution and conservative batches. The upstream model's advertised 32K context does not change this runtime cap. Weights are downloaded on first use, not bundled with installers.

## Encoding contract

Documents have no instruction prefix. Queries use `Instruct: Retrieve relevant source code, documentation, and engineering decisions for the query\nQuery: <query>`. `model.query_instruction` overrides the task text. Qwen uses the last non-padding token; both left and right padding work. Its decoder export receives empty past-key/value tensors for each full-sequence encoding pass. Only the requested hidden-state output is returned; decoder cache outputs are discarded. Vectors are checked for dimension, finite values and nonzero norm, then L2-normalized. Qwen dimensions from 32 through the exported width can be truncated and renormalized; changing dimensions requires a new index.

The default artifact is `onnx/model_quantized.onnx`, about 614 MB, at revision `c25a394dd583836952667c12f008335071b3f43d`. Its SHA256 is checked against the source-controlled profile. Tokenizer and graph use the same revision. Models are materialized together under `data/models/embeddings/<repo-and-revision-key>/`; this avoids external-weight symlink failures. CPU fallback reuses that graph. Custom repositories use `main` unless `model.revision` is explicitly pinned; only the default artifact has a built-in graph checksum. Pin custom revisions explicitly: updates to a mutable `main` reference cannot be detected by the configuration fingerprint alone.

The adapter supports `input_ids`, `attention_mask`, optional `token_type_ids`/`position_ids`, and the Qwen decoder cache contract. Named sentence-embedding outputs are preferred over token outputs; `model.output` selects an explicit output. Unknown required inputs fail visibly. Arbitrary multimodal ONNX exports are not interchangeable text encoders. BGE-M3 retains CLS pooling; other custom models default to mean pooling. EmbeddingGemma-300M infers 768 dimensions if `model.dim` is not explicitly set, but its legacy prompt contract is not newly implemented here. Review and test any custom adapter before use.

## Rebuild after upgrading

Existing explicit environment/config settings take precedence over these defaults. Restart MCP/dashboard processes after model configuration changes. To select the new embedding profile explicitly:

```bash
imprint config set model.name onnx-community/Qwen3-Embedding-0.6B-ONNX
imprint config set model.revision auto
imprint config set model.file auto
imprint config set model.dim 1024
imprint config set model.pooling auto
imprint config set model.output auto
imprint config set model.query_instruction auto
imprint config set model.device cpu
imprint config set model.seq_length 2048
imprint config set model.batch_size 0
imprint workspace switch qwen-v1
imprint ingest /path/to/project --batch-size 1
```

A new workspace preserves the old workspace's notes, graph and vectors. Reimport conversations, documents, URLs and durable memories you want available there; indexing a code directory does not reconstruct manually stored notes. Workspace migration and vector sync copy existing vectors and cannot convert an old embedding index. Back up irreplaceable memories before choosing to wipe instead. No upgrade automatically wipes, switches workspaces or starts a rebuild.

Qdrant collection metadata stores an embedding fingerprint covering repository, revision, graph, dimensions, pooling, query instruction, output selection, token cap and encoder version. The runtime refuses a different profile, including equal-dimension changes. Nonempty legacy collections without identity require an explicit rebuild; empty compatible collections can acquire identity. Topic prototypes have separate caches keyed by this fingerprint and label inventory, so old prototypes are not reused.

For a complete refresh **within the same profile**, use:

```bash
imprint ingest /path/to/project --force --batch-size 1
# Or:
imprint refresh /path/to/project --force --batch-size 1
```

`--force` bypasses both unchanged-file hashing and logical-ID vector dedup. It also works for single-file ingest and queued ingest/refresh (`force: true`, optional `batch_size`). The CLI batch size controls source flush grouping; `model.batch_size` controls actual ONNX inference batches. It preserves source replacement's upsert-before-prune behavior. It cannot resize or relabel an incompatible collection. Indexing failures exit nonzero through the Go CLI rather than printing a successful completion.

Snapshot imports and precomputed sync require matching embedding identity. Legacy or mismatched vector transfers fail before vector writes; reindex/export from the source using matching settings. Ordinary profile metadata is an interoperability check, not authentication of an untrusted sync peer.

## Resource controls

| Setting | Default | Behavior |
| --- | --- | --- |
| `model.device` | `cpu` | `auto` and `gpu` remain explicit choices; CUDA requires the GPU runtime profile. |
| `model.batch_size` | `0` | Auto uses 1 on CPU/auto and 4 on explicit GPU; raise after measuring memory. |
| `model.seq_length` | `2048` | Truncates model input; long documents are chunked beforehand. |
| `model.threads` | `4` | CPU inference threads. |
| `model.gpu_mem_mb` | `2048` | ORT CUDA arena limit, not a cap on total process or GPU use. |
| `model.gpu_device` | `0` | CUDA device index. |

Length-bucketed batches reduce padding. ONNX memory arenas/pattern caching are disabled, and intermediate buffers are collected between batches. CUDA errors trigger batch halving, then CPU fallback for a single item. Auto batching stays conservative even when `model.device=auto` discovers CUDA. Model downloads and runtime overhead still require memory and disk beyond the artifact size.

See [local model analysis](local-model-selection.md), [helper setup](local-models.md), [configuration](configuration.md) and accepted [ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md). Overall retrieval quality and native GPU/macOS/Windows model inference still require separate evaluation.
