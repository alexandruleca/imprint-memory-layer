---
title: Embeddings
description: Pinned Qwen3 embeddings on llama.cpp, GPU placement, model identity and rebuilding an index.
---

Imprint embeds code, documents, decisions and conversations locally. The default is [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), Apache-2.0, through Qwen's own [GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF), run by llama.cpp (llama-cpp-python 0.3.36). It runs on a GPU when llama.cpp finds one and on the CPU otherwise ([GPU embeddings](#gpu-embeddings), [ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). The runtime uses 1024 dimensions and a 2048-token cap. The upstream model's advertised 32K context does not change this runtime cap. Weights are downloaded on first use, not bundled with installers.

## Encoding contract

Documents have no instruction prefix. Queries use `Instruct: Retrieve relevant source code, documentation, and engineering decisions for the query\nQuery: <query>`. `model.query_instruction` overrides the task text. Qwen reads its vector at the last token: the GGUF's tokenizer appends the end-of-text token, and each input is capped at `model.seq_length` tokens including it, so a long input loses text from its end and keeps that token. Before tokenizing, text is cut to 8 characters per token of the cap (16,384 characters by default). Vectors are checked for dimension, finite values and nonzero norm, then L2-normalized. Qwen dimensions from 32 through the model's width can be truncated (Matryoshka) and renormalized; changing dimensions requires a new index.

The default artifact is `Qwen3-Embedding-0.6B-Q8_0.gguf`, 639 MB, at revision `370f27d7550e0def9b39c1f16d3fbaa13aa67728`. Its SHA256 is checked against the source-controlled profile in [`model_profiles.py`](../imprint/model_profiles.py). The GGUF carries its tokenizer. Models are stored under `data/models/embeddings/<repo-and-revision-key>/`. Custom repositories use `main` unless `model.revision` is explicitly pinned, and must name their GGUF file in `model.file`; only the default artifact has a built-in checksum. Pin custom revisions explicitly: updates to a mutable `main` reference cannot be detected by the configuration fingerprint alone.

Q8_0 matches the full-precision model closely. On 160 real conversation chunks (1,726 tokens on average) and 8 queries, compared with the full-precision model run through transformers in fp32, Q8_0 on an RTX 4080 Laptop GPU had a mean cosine of 0.9994 (minimum 0.995), and its query top 10 matched the full model's 100%; on the CPU, 0.9990 and 95%. The int8 ONNX export used before: 0.84 (minimum 0.74) and 58% ([ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)).

Other models need a GGUF that llama.cpp runs as an embedding model. `model.pooling=auto` uses the last token for Qwen3, CLS for BGE-M3 and the GGUF's own pooling metadata for any other model; `cls`, `mean` and `last` override it. Matryoshka truncation applies to the Qwen3 family only; for other models `model.dim` must equal the model's width. EmbeddingGemma-300M infers 768 dimensions if `model.dim` is not explicitly set, but its legacy prompt contract is not implemented here. Review and test any custom model before use.

## Rebuild after upgrading

Existing explicit environment/config settings take precedence over these defaults. Restart MCP/dashboard processes after model configuration changes. A release that changes the embedding profile, as the move to llama.cpp did, refuses a collection built by an earlier one with `Embedding profile mismatch`; reindex into a new workspace. `imprint config` lists your overrides. To return to the default profile, reset the `model.*` overrides you did not choose deliberately, then build a new workspace:

```bash
imprint config reset model.name
imprint config reset model.revision
imprint config reset model.file
imprint config reset model.dim
imprint config reset model.pooling
imprint config reset model.query_instruction
imprint config reset model.device
imprint config reset model.seq_length
imprint config reset model.batch_size
imprint workspace switch qwen-gguf
imprint ingest /path/to/project
```

A new workspace preserves the old workspace's notes, graph and vectors. Reimport conversations, documents, URLs and durable memories you want available there; indexing a code directory does not reconstruct manually stored notes. Workspace migration and vector sync copy existing vectors and cannot convert an old embedding index. Back up irreplaceable memories before choosing to wipe instead. No upgrade automatically wipes, switches workspaces or starts a rebuild.

In the dashboard, **Memory index needs rebuilding** reports this compatibility conflict and leaves the workspace menu available to create and switch to a new workspace. The API returns HTTP 409 with `error: "embedding_profile_conflict"` and the rebuild explanation. Earlier releases returned HTTP 500 while the dashboard said **Failed to connect to API** and suggested `imprint ui start`; restarting the server cannot change a collection's embedding profile. For those releases, `imprint workspace switch qwen-gguf` followed by reloading the dashboard selects a fresh index while keeping the old one; choose another new name if that workspace already exists. Then reindex and reimport the sources you need.

Qdrant collection metadata stores an embedding fingerprint covering repository, revision, file, dimensions, pooling, query instruction, token cap and encoder version. The runtime refuses a different profile, including equal-dimension changes. Nonempty legacy collections without identity require an explicit rebuild; empty compatible collections can acquire identity. Topic prototypes have separate caches keyed by this fingerprint and label inventory, so old prototypes are not reused.

For a complete refresh **within the same profile**, use:

```bash
imprint ingest /path/to/project --force
# Or:
imprint refresh /path/to/project --force
```

`--force` bypasses both unchanged-file hashing and logical-ID vector dedup. It also works for single-file ingest and queued ingest/refresh (`force: true`, optional `batch_size`). The CLI batch size controls source flush grouping; `model.batch_size` controls the texts per embedding call. It preserves source replacement's upsert-before-prune behavior. It cannot resize or relabel an incompatible collection. Indexing failures exit nonzero through the Go CLI rather than printing a successful completion.

Snapshot imports and precomputed sync require matching embedding identity, so paired devices must run the same release with the same `model.*` settings. Legacy or mismatched vector transfers fail before vector writes; reindex/export from the source using matching settings. Ordinary profile metadata is an interoperability check, not authentication of an untrusted sync peer.

## Resource controls

| Setting | Default | Behavior |
| --- | --- | --- |
| `model.device` | `auto` | `auto` embeds on a GPU when llama.cpp finds one and on the CPU otherwise. `cpu` forces the CPU. `gpu` asks for the GPU, and warns and embeds on the CPU when there is none. Changing it installs nothing; running processes use it once they restart. |
| `model.gpu_device` | `auto` | The GPU that runs embeddings and the local LLM. `auto` takes the discrete GPU with the most memory, and an integrated GPU only when there is no discrete one; a number is an index into the GPUs llama.cpp offloads to, which Settings lists ([ADR 0087](adr/0087-llama-cpp-runs-each-model-on-one-gpu.md)). |
| `model.batch_size` | `0` | Texts per embedding call. Auto uses 8 on a GPU, where llama.cpp packs them into one pass, and 1 on the CPU, so progress moves after every text. |
| `model.seq_length` | `2048` | Token cap per input, end-of-text token included; long documents are chunked beforehand. |
| `model.threads` | `0` | CPU threads for CPU embedding and the privacy filter; `0` uses half the logical CPUs. |

Texts are batched by similar length. Each process that embeds (every MCP server, the dashboard, each learner) loads its own copy of the model, about 0.6 GB plus a compute buffer, and runs one embedding call at a time. A GPU that cannot load the model, for example one with too little free memory, leaves it on the CPU with a note on standard error. Model downloads and runtime overhead still require memory and disk beyond the artifact size.

## GPU embeddings

Embeddings run on any GPU llama.cpp's installed build supports: Vulkan on Windows x64 and Linux x64, for NVIDIA, AMD and Intel GPUs through the graphics driver alone, and Metal on macOS. No CUDA, toolkit or vendor library is installed. The install profile picks the build: `gpu` and `auto` install the GPU build and keep it when llama.cpp finds a GPU, and the CPU build otherwise ([installation](installation.md#llamacpp-and-onnx-runtime)). Linux arm64 and any other platform embed on the CPU. The local LLM uses the same build and the same `model.gpu_device` ([local models](local-models.md)).

Each model runs whole on one GPU ([ADR 0087](adr/0087-llama-cpp-runs-each-model-on-one-gpu.md)). llama.cpp would otherwise split its layers across every discrete GPU, and a slow card paces a fast one. Measured on an i9-13900HX laptop with an RTX 4080 Laptop GPU and a GTX 1060, embedding the 160 chunks above:

| Runtime | Tokens/s |
| --- | --- |
| llama.cpp Vulkan, RTX 4080 Laptop alone | 7,316 (5,525 through Imprint's own code) |
| llama.cpp Vulkan, its default split over both GPUs | 1,763 |
| llama.cpp Vulkan, GTX 1060 alone | 632 |
| llama.cpp CPU (16 threads) | 438 |
| Earlier int8 ONNX export, ONNX Runtime CPU (WSL, 16 threads) | 291 |

Settings → Embeddings → Device says what `auto` and `gpu` do on this device, and `model.gpu_device` lists the GPUs llama.cpp found. `imprint doctor` reports where embeddings run. When setup found no GPU, it installed the CPU build: after installing or updating the graphics driver, run `imprint setup` again on the `gpu` or `auto` profile.

Changing the device does not change the embedding fingerprint, so an existing index keeps working. GPU and CPU vectors of the same text are close but not bit-identical; reindex with `--force` after switching when consistent ranking matters.

See [local model analysis](local-model-selection.md), [helper setup](local-models.md), [configuration](configuration.md), accepted [ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md), [ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md) and [ADR 0087](adr/0087-llama-cpp-runs-each-model-on-one-gpu.md). Overall retrieval quality, and embedding on macOS and on AMD or Intel GPUs, still require separate evaluation.
