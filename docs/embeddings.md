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
| `model.device` | `cpu` | The default int8 model ran faster on CPU than through CUDA (RTX 4080 Laptop: 1.4 s against 2.4 s per 4,000-character exchange). `gpu` and `auto` are explicit choices for fp16 models such as BGE-M3; see [GPU embeddings](#gpu-embeddings). |
| `model.batch_size` | `0` | Auto uses 1 on CPU/auto and 4 on explicit GPU; raise after measuring memory. |
| `model.seq_length` | `2048` | Truncates model input; long documents are chunked beforehand. |
| `model.threads` | `4` | CPU inference threads. |
| `model.gpu_mem_mb` | `2048` | ORT CUDA arena limit, not a cap on total process or GPU use. |
| `model.gpu_device` | `auto` | CUDA device index. `auto` skips GPUs older than the CUDA runtime supports (CUDA 13 needs compute capability 7.5+) and embeds on CPU when none is left. The dashboard lists the detected GPUs. Setup installs the CUDA runtime only when the selected GPU is supported. |

Length-bucketed batches reduce padding. ONNX memory arenas/pattern caching are disabled, and intermediate buffers are collected between batches. CUDA errors trigger batch halving, then CPU fallback for a single item. Auto batching stays conservative even when `model.device=auto` discovers CUDA. Model downloads and runtime overhead still require memory and disk beyond the artifact size.

## GPU embeddings

Embeddings run on an NVIDIA GPU through ONNX Runtime's CUDA build. Imprint installs that build, with the NVIDIA libraries its CUDA provider loads (CUDA runtime, cuBLAS with NVRTC, cuDNN, and cuRAND on Linux; about 1.1 GB on Windows and 1.4 GB on Linux), only when `model.device` is `gpu` or `auto` and this machine can use it ([ADR 0062](adr/0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)):

- Windows x64, Linux x64 or Linux arm64; macOS has no CUDA.
- An NVIDIA driver that supports CUDA 13 (R580 or newer).
- A GPU of compute capability 7.5 or newer (Turing and later): the one `model.gpu_device` names, or for `auto` the first such GPU.
- No CUDA failure recorded for this machine and build ([installation](installation.md#embedding-runtime-and-cuda-failures)).

Anywhere else, including machines with only AMD or Intel GPUs, embeddings stay on the CPU and nothing CUDA is downloaded. The local LLM still uses those GPUs through llama.cpp's Vulkan build ([local models](local-models.md)).

`gpu` and `auto` install the same runtime. At run time `auto` uses CUDA whenever the CUDA build is installed and a supported GPU is present, and auto batching keeps 1 item per batch; `gpu` batches 4, and for BGE-M3 `model.file=auto` picks its fp16 export. Where GPU embedding cannot work, Settings marks `gpu` unavailable with the reason, `auto` says it embeds on the CPU here, `imprint config set model.device gpu` prints the reason, and `imprint doctor` and `imprint setup` report it.

**Turning it on.** In the dashboard, choose GPU (or Auto) under Settings → Embeddings → Device. The server starts `imprint bootstrap`, which waits for a running job, restarts the dashboard, downloads and installs the CUDA build and checks CUDA on the GPU; the dashboard comes back on it. Its output goes to `data/bootstrap.log`, and Settings shows a runtime that is still missing, with the end of that log and an Install button. From the command line, `imprint config set model.device gpu` says what it will install, and `imprint setup` installs it. Choosing CPU again removes the CUDA libraries and frees their disk space. On Windows, setup stops Imprint's MCP servers before it replaces the runtime, and AI clients reconnect Imprint; on Linux and macOS they switch when they next start ([ADR 0084](adr/0084-setup-frees-the-runtime-before-replacing-its-files.md)).

Changing the device does not change the embedding fingerprint, so an existing index keeps working; for a model whose file depends on the device (BGE-M3 with `model.file=auto`), pin `model.file` first. CUDA and CPU vectors of the same text are close but not equal: on 4,000-character passages of this page, the lowest cosine between them was 0.91 for the default int8 model and 0.97 for BGE-M3 fp16 (RTX 4080 Laptop, ONNX Runtime 1.31). An index mixing both ranks less consistently; reindex with `--force` after switching when that matters.

See [local model analysis](local-model-selection.md), [helper setup](local-models.md), [configuration](configuration.md) and accepted [ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md). Overall retrieval quality and native GPU/macOS/Windows model inference still require separate evaluation.
