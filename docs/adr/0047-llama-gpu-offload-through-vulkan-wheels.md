---
title: ADR 0047 — llama.cpp GPU offload through upstream's Vulkan wheels
description: The gpu and auto profiles install llama-cpp-python's hash-locked Vulkan build and keep it only when llama.cpp finds a GPU, replacing the nvcc rebuild.
---

- Status: Accepted
- Date: 2026-10-24
- Related: ADR 0032, ADR 0035, ADR 0038

## Context

On a Windows install with an RTX 4080, a GTX 1060 and an i9-13900HX, `imprint learn` stored 73 of 140 conversations in about six hours, with `tagger.llm` on. Measured per exchange (about 1,500 prompt tokens), the local Qwen3.5-4B tagger took 38 s on CPU, against about 1.5 s for the embedding. So LLM tagging was about 95% of ingest time.

The tagger ran on CPU because llama-cpp-python had no GPU offload. On the gpu and auto profiles, setup rebuilt it from PyPI source with `CMAKE_ARGS=-DGGML_CUDA=on`, which needs the CUDA toolkit's `nvcc`. That machine had no toolkit, so the rebuild failed. The failure was recorded in `data/gpu_state.json` and never retried. That path was also the one unpinned, unhashed install left (ADR 0035 kept it "unchanged"). It only ever covered NVIDIA.

The maintainer publishes prebuilt GPU wheels of the pinned version as release assets with sha256 digests: `v0.3.36-vulkan` (Windows x64 40 MB, manylinux x86_64 121 MB) and `v0.3.36-cu130`/`-cu132` (Windows x64 217 MB, manylinux_2_35 x86_64 746 MB), among others.

Measured on that machine with Imprint's own tagging call, a different exchange each time, no grammar change:

| Build | Seconds per exchange |
| --- | --- |
| CPU wheel, 4 threads | 38.4 |
| Vulkan, model split over both NVIDIA GPUs (llama.cpp default) | 8.0 |
| Vulkan, whole model on the RTX 4080 | about 8 (no faster than the split) |
| CUDA 13, RTX 4080 only | 4.7 |
| CUDA 13, all GPUs visible | aborts: `ggml-cuda.cu: CUDA error` (CUDA 13 has no code for the 1060) |

Two facts about the wheels shape the design:

- **The Vulkan build links the Vulkan loader directly.** On Windows, `ggml.dll` imports `ggml-vulkan.dll`, which imports `vulkan-1.dll`. GPU drivers install the loader on Windows and desktop Linux, but servers and containers often lack it, and the binding then fails to import.
- **`llama_supports_gpu_offload()` asks llama.cpp's device registry.** It is true only when a GPU device was found.

## Decision

`requirements/llm-gpu.txt` is the GPU build of the LLM overlay, with its own hash lock, `llm-gpu.lock.txt`:

- Windows x64 and Linux x64 use the `v0.3.36-vulkan` wheels.
- Every other platform matches `llm.txt`: macOS builds from source with Metal, and Linux arm64 uses the CPU wheel.

The overlay has the same version and dependency pins as `llm.lock.txt`, so uv swaps the two builds by wheel URL.

`installLLMOverlay` installs the GPU build on the gpu and auto profiles, then runs `llama_supports_gpu_offload()` in the venv:

- When it prints `True`, the GPU build stays.
- When it prints `False` or the import fails, setup installs `llm.lock.txt`.

The cpu profile installs `llm.lock.txt` directly. Setup checks this on every run, and nothing is recorded: swapping is a cached wheel install, and a machine that gains a GPU or driver picks it up on the next setup.

The nvcc rebuild is removed, along with its toolkit discovery, compute-capability parsing, the `llama_cuda_failed` record and the CUDA toolkit install hints. `data/gpu_state.json` keeps only the ONNX Runtime verdict. `--retry-gpu` clears that verdict as before.

Runtime code is unchanged. `n_gpu_layers=-1` offloads every layer, and llama.cpp picks the devices: all discrete GPUs, or the integrated GPU when there is no discrete one.

## Alternatives

- **The CUDA 13 wheel.** It is about 1.7× faster than Vulkan on the RTX 4080. But it covers only NVIDIA Turing and newer, and it needs `cublas64_13.dll` from the GPU embedding runtime (loaded first with `onnxruntime.preload_dlls()`). It also aborts when an unsupported GPU is visible, which would need `CUDA_VISIBLE_DEVICES` filtering. It could later become a second GPU build for NVIDIA machines on the GPU embedding runtime.
- **Keep the nvcc rebuild.** It needs a multi-gigabyte toolkit and a matching compiler, it is unhashed, and it covers NVIDIA only.
- **Run `llama-server` behind the OpenAI-compatible provider.** That adds a managed service, which ADR 0035 already rejected.
- **Pin the model to the fastest GPU (`split_mode=NONE`).** On the measured machine Vulkan enumerated the GTX 1060 first, so a fixed `main_gpu` picked the slow card (17 s). Picking a card needs a speed heuristic and was no faster than llama.cpp's default split.

## Consequences

- On the measured machine, LLM tagging is about 4.8× faster with no toolkit or compiler. AMD and Intel GPUs get offload for the first time.
- An integrated-only machine offloads to its iGPU. Setting `tagger.local.n_gpu_layers` and `chat.n_gpu_layers` to `0` restores CPU inference.
- The JSON schema grammar in `local_llm.complete_json` costs about 3–4 s per exchange on GPU: 8.0 s against 4.1 s without it on Vulkan, and 4.7 s against 1.7 s on CUDA. Every ungrammared output in those runs was valid JSON. Dropping the grammar is a separate decision.
- Upgrading the binding means updating the version in every URL of both overlays and recompiling both locks from scratch.
- macOS and platforms without a prebuilt wheel still build from source, as before.

## Validation

- Wheels: the published sha256 digests matched the downloads, both archives passed `zipfile.testzip`, and their DLL/ELF imports were inspected.
- Lock: `llm-gpu.lock.txt` was compiled with the pinned uv 0.5.11. Its non-llama pins and hashes are identical to `llm.lock.txt`.
- Switching: in a fresh Linux venv, uv switched CPU → Vulkan → CPU by URL with plain `uv pip install --require-hashes`, as `direct_url.json` and the installed `libggml-vulkan.so` showed.
- Windows: the Windows uv installed `llm-gpu.lock.txt` with `--require-hashes` into a fresh venv. Setup's offload check printed the three Vulkan devices and `True`.
- Go: `TestInstallLLMOverlayKeepsTheGPUBuildOnlyWhenItOffloads` covers four cases with a fake pip and python:
  - the cpu profile
  - offload found
  - no GPU
  - failed import
- Not yet verified: a full `imprint setup` on Windows from a release build; Linux desktops with AMD or Intel GPUs; machines without a Vulkan loader, where the fallback is covered only by the unit test.
