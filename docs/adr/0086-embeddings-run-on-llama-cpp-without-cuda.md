---
title: ADR 0086 — Embeddings run on llama.cpp, without CUDA
description: Embeddings use Qwen's Q8_0 GGUF of Qwen3-Embedding-0.6B through llama.cpp, whose Vulkan or Metal build runs them on any GPU and whose CPU build runs them elsewhere; the CUDA runtime and the int8 ONNX export are removed.
---

- Status: Amended
- Date: 2026-10-27
- Supersedes:
  - [ADR 0038](0038-gpu-runtime-ships-cuda-libraries.md)
  - [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)
- Amends:
  - [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) — the default embedding export (Qwen's Q8_0 GGUF instead of the int8 ONNX export) and device (`auto`)
  - [ADR 0012](0012-native-package-runtime-and-silent-installs.md) — silent installs choose only the profile; the local LLM binding is no longer optional
  - [ADR 0032](0032-one-onnx-runtime-per-environment.md) — ONNX Runtime's CPU build serves OCR and the privacy filter only, and setup removes any other build
  - [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) — the binding is part of every installation; the first local chat only downloads the model
  - [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) — Settings no longer starts `imprint bootstrap`
- Amended by: [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) — macOS installs prebuilt wheels and needs no compiler
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md)

## Context

On the user's Windows laptop (i9-13900HX, RTX 4080 Laptop GPU, GTX 1060 6 GB, driver 581.80):

- **GPU embedding did not work reliably from the packaged install.** The user reported never getting CUDA to work properly with a packaged install, and only sometimes with the CLI installer. On 2026-10-26 switching `model.device` to `gpu` in Settings started `imprint bootstrap`. It restarted the dashboard (ADR 0084), then prepared the CUDA wheels for 3 min 11 s while the dashboard was down. The dashboard did not come back: `imprint_ui.log` shows the restarted service starting at 19:06:16Z and nothing more until the user reopened the app at 19:16:14Z. ONNX Runtime 1.31 also printed `No registered plugin EP device found for 'CUDAExecutionProvider'` on every CUDA session.
- **Learning was slow while the GPU sat idle.** An overnight learn of about 150 conversations finished half of them in 12 hours, with LLM tagging deferred (ADR 0067). The user's 112 Claude Code transcripts in WSL produce 13,652 chunks, 73.8 million characters, and a random sample of 160 chunks averages 1,726 tokens. ADR 0062 had measured the int8 export at 2.4 s per 4,000-character exchange on the RTX 4080 against 1.4 s on the CPU: its quantized operators suit CUDA poorly. `nvidia-smi` showed the learner on the RTX 4080 at 0–48% utilization.
- **The int8 export does not match the model.** The 160 chunks and 8 queries were embedded by Qwen3-Embedding-0.6B in full precision (transformers 4.57.6 and torch 2.14.1, CPU, fp32, last-token pooling, 2,048-token cap) as ground truth, and by each candidate:

| Engine | Cosine to fp32, min / mean | Query top 10 as fp32 | Document 10 nearest as fp32 | Tokens/s |
| --- | --- | --- | --- | --- |
| ONNX int8 export (the default), CPU, 16 threads, in WSL | 0.743 / 0.842 | 58% | 67% | 291 |
| GGUF Q8_0, llama.cpp Vulkan, RTX 4080 only | 0.995 / 0.9994 | 100% | 97% | 7,316 |
| GGUF f16, llama.cpp Vulkan, RTX 4080 only | 0.994 / 0.9997 | 100% | 98% | 7,476 |
| GGUF Q8_0, llama.cpp CPU, 16 threads | 0.993 / 0.9990 | 95% | 97% | 438 |

- **llama.cpp's Vulkan build needs only the graphics driver.** Upstream's hash-locked `v0.3.36-vulkan` wheel (ADR 0049) installed into a fresh venv in 8 s and found all three GPUs: the GTX 1060, the integrated Intel GPU and the RTX 4080 with `NV_coopmat2` matrix cores. It covers NVIDIA, AMD and Intel GPUs on Windows and Linux; the macOS build of the binding uses Metal.

## Decision

**Embeddings run on llama.cpp.** The default model is Qwen's own `Qwen/Qwen3-Embedding-0.6B-GGUF`, file `Qwen3-Embedding-0.6B-Q8_0.gguf` (639 MB), pinned by revision and SHA-256. `imprint/embeddings.py` loads it with `embedding=True`, last-token pooling, and a context, batch and micro-batch of `model.seq_length` (2,048) tokens. The GGUF's tokenizer appends the end-of-text token at which the model reads its vector, so an input longer than the cap loses text from its end, never that token. The query instruction, the Matryoshka cut to `model.dim` and L2 normalization are unchanged. Other models are GGUF repos named with their `model.file`; `model.pooling=auto` follows the model family (last token for Qwen, CLS for BGE-M3) and otherwise the GGUF's own metadata.

**One binding for every GPU.** The llama.cpp build that setup installs runs embeddings and the local LLM alike: the GPU build where llama.cpp finds a GPU, the CPU build otherwise, chosen by the install profile as ADR 0049 describes. `model.device` defaults to `auto`, the GPU when there is one; `cpu` keeps embeddings on the CPU; `gpu` asks for the GPU and says when there is none. Changing it installs nothing. Which GPU runs a model is [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md).

**CUDA is removed.** `requirements/gpu.txt` and its lock, `imprint/gpu_runtime.py`, `imprint/cuda_devices.py`, `data/gpu_state.json`, the CUDA check, `--retry-gpu`, `model.gpu_mem_mb` and `model.output` are gone. ONNX Runtime's CPU build remains for OCR and the opt-in privacy filter, and `model.threads` (default: half the logical CPUs) sets the CPU threads of both local runtimes. Setup removes any other ONNX Runtime build an installation still holds, with what only that build brought in, such as `onnxruntime-gpu` and its NVIDIA wheels (`python -m imprint.onnx_runtime unused`).

**The binding is part of every installation.** `--with-llm`, `--no-llm`, `IMPRINT_WITH_LLM`, `imprint profile add-llm` and `drop-llm`, `with_llm` in `profile.json`, and the installers' LLM questions are removed, and a failed binding install fails setup. The first local chat only downloads its model; a binding that does not load is reported as a damaged installation. Settings no longer starts `imprint bootstrap`, because no device change replaces the runtime.

**Loading keeps standard output untouched.** The binding's quiet mode points file descriptors 1 and 2 at the null device while a model loads, and an MCP server's standard output carries the protocol. Models therefore load with `verbose=True`; llama.cpp's log goes through a callback that passes on errors only, and what the binding prints during a load goes to a buffer that only a failed load reports.

## Rejected alternatives

- **Keep CUDA as an option beside Vulkan.** The user would accept both. On the RTX 4080, Vulkan reaches 7,316 tokens/s with vectors that match the model. CUDA would bring back 1.1–1.5 GB of NVIDIA wheels, the driver and compute-capability gates, the runtime swap and its downtime, for an embedding speedup nobody measured. ADR 0049 measured the 4B LLM 1.7 times faster on the binding's CUDA 13 wheel than on Vulkan; with embeddings on llama.cpp, CUDA would be another build of the same binding, as ADR 0049's invalidation condition foresees, not another engine.
- **Keep ONNX Runtime and run embeddings on the CPU.** At the best measured CPU rate (ADR 0062: 1.37 s per 4,000-character exchange with 16 threads, about 730 tokens/s) the user's 23 million tokens take about 9 hours, and the int8 export's top 10 matches the model's only 58% of the time.
- **ONNX Runtime with a full-precision export on CUDA.** It fixes the vectors but keeps CUDA, and serves NVIDIA GPUs only.
- **ONNX Runtime's DirectML build.** It runs on Windows only and is a third runtime, as ADR 0062 noted; llama.cpp's Vulkan build covers the same GPUs on Windows and Linux.
- **The f16 GGUF.** It is as fast on the RTX 4080 (7,476 tokens/s) and slightly closer to the model (mean cosine 0.9997 against 0.9994), but twice the download and memory (1.2 GB).
- **ONNX Runtime on the CPU and llama.cpp on the GPU.** Two engines would write vectors of one collection, so the embedding identity would have to name the engine per device; one engine serves both.
- **Larger batches.** A context, batch and micro-batch of 8,192 tokens ran at 1,006 tokens/s, and the f16 model's working set grew to 18 GB, against 7,316 tokens/s at 2,048.

## Consequences

- Embedding the user's 13,652 chunks takes under an hour on the RTX 4080 instead of 9–15 hours, and search follows the actual model.
- The embedding identity changes (encoder version 2, the GGUF repo and file). Existing collections are refused as ADR 0010 specifies and are reindexed into a new workspace. Paired devices must all run a release with this change, since sync carries vectors and checks their identity.
- Each process that embeds holds its own copy of the model on the GPU, about 0.6 GB plus a compute buffer: every MCP server, the dashboard and each learner. A GPU that cannot hold it leaves that process on the CPU, with a line on standard error.
- Every installation downloads the binding (the Vulkan wheel is 40 MB on Windows and 121 MB on Linux) and the 639 MB model on first use. No installation downloads CUDA libraries.
- macOS still builds the binding from source, which needs the Xcode Command Line Tools. This repository will build and publish prebuilt macOS wheels on its own runners, and the locks will pin them like upstream's.
- Venvs from before keep `onnx` and `ml-dtypes`, about 20 MB, which only the CUDA check used. Nothing requires them and nothing names them, so setup cannot tell them from a user's packages.
- On a machine without a usable GPU, embedding runs on llama.cpp's CPU build: 438 tokens/s with 16 threads on this laptop's Windows, against 291 for the int8 export under WSL.
- The runtime handling of ADR 0084 stays for updates that replace packages; a device change no longer takes the dashboard down.

## Invalidation conditions

- If upstream stops publishing hash-locked Vulkan wheels of the binding for Windows or Linux x64, GPU embedding there would need a compiler; build those wheels here or revisit the engine.
- If prebuilt macOS wheels of the binding cannot be built and pinned, macOS installs keep requiring a compiler; revisit how macOS gets the binding.
- If the binding's CUDA build is measured substantially faster for embeddings on NVIDIA GPUs, add it as another build of the same binding chosen by setup, not as another engine.
- If Qwen republishes the default GGUF or a better default model appears, repeat the comparison with the full-precision model before pinning it.
- If llama-cpp-python's quiet mode stops redirecting file descriptors, or its load-time output moves off standard error, revisit how loads are kept off standard output.

## Validation

- All measurements above ran natively on the user's Windows laptop, in scratch venvs under `%TEMP%` built with the pinned uv 0.5.11 and Python 3.12.8: 160 chunks sampled with seed 7 from the user's 112 WSL Claude Code transcripts, and 8 queries.
- Imprint's own embedding path end to end, against the same full-precision vectors:
  - Linux (WSL), CPU wheel, 10 of the chunks: cosine at least 0.9992 for documents and 0.9991 for queries.
  - Native Windows, Vulkan wheel, all 160 chunks: 50 s, 5,525 tokens/s, cosine mean 0.9996 and minimum 0.9991, queries at least 0.9996; `llama_runtime` placed the model on the RTX 4080.
- Unit tests: `tests/test_models.py` (pooling choice, truncation that keeps the end-of-text token, projection and normalization, malformed output, the local LLM loading with `verbose=True`), `tests/test_llama_runtime.py` (quiet loading and logs, CPU fallback), `tests/test_onnx_runtime.py` (removal of a CUDA build and its NVIDIA libraries), `tests/test_chat_stream.py` (download-only first chat, damaged binding), `cmd/setup_test.go` and `cmd/llama_install_test.go`.
- Not verified: macOS (the source build and Metal), Linux desktops with AMD or Intel GPUs, the Windows installer and `imprint update` from a release with the CUDA runtime, and several MCP servers sharing one small GPU.
