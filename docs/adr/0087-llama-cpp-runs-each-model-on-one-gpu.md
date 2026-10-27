---
title: ADR 0087 — llama.cpp runs each model on one GPU
description: Embeddings and the local LLM load whole onto one GPU, the one model.gpu_device names or by default the discrete GPU with the most memory, instead of llama.cpp's split across every GPU.
---

- Status: Accepted
- Date: 2026-10-27
- Amends:
  - [ADR 0045](0045-setting-choices-and-gpu-auto-select.md) — `model.gpu_device` indexes the GPUs llama.cpp offloads to, and `auto` is the discrete GPU with the most memory
  - [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) — a model no longer splits across GPUs
- Related: [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md)

## Context

llama.cpp splits a model's layers across every discrete GPU by default. ADR 0049 kept that for the local LLM: on a laptop with an RTX 4080 and a GTX 1060, generation took about 8 s per tagging call either split or on the 4080 alone. It rejected pinning one GPU because Vulkan listed the GTX 1060 first, so a fixed `main_gpu` picked the slow card (17 s).

Embedding is prompt processing only, and there the split costs much more. Measured on that laptop with Qwen3-Embedding-0.6B Q8_0 over 160 real chunks (ADR 0086):

| Placement | Tokens/s |
| --- | --- |
| RTX 4080 alone | 7,316 |
| Split over both NVIDIA GPUs (llama.cpp's default) | 1,763 |
| GTX 1060 alone | 632 |

Two facts shape the choice:

- **`GGML_VK_VISIBLE_DEVICES` cannot name a GPU.** Its indices follow Vulkan's raw enumeration: indices 0 and 1 both gave the GTX 1060, 2 the integrated Intel GPU, 3 and 4 the RTX 4080 (index 4 ran at a tenth of the speed), and 5 found nothing.
- **ggml's device registry describes the GPUs.** It reports each device's type (discrete, integrated, CPU), description and memory. llama.cpp offloads to the discrete GPUs in registry order, or to the integrated ones when there is no discrete GPU, and `main_gpu` indexes that list. The integrated GPU reports 16 GB, its share of system memory.

## Decision

`imprint/llama_runtime.py` lists the GPUs llama.cpp offloads to from ggml's registry, in llama.cpp's order. The binding loads the registry library but does not wrap these calls, so they go through `ctypes`. Every model, embeddings and the local LLM alike, loads whole onto one GPU with `split_mode` NONE and that GPU as `main_gpu`:

- `model.gpu_device=auto`, the default, takes the GPU with the most memory. Integrated GPUs are candidates only when there is no discrete one, as in llama.cpp's own list, so their shared memory never outranks a discrete card.
- An index names a GPU in that list. Settings lists the GPUs by index, name and memory.
- With no GPU chosen, or with `n_gpu_layers` 0, the model runs on the CPU.

## Rejected alternatives

- **llama.cpp's default split.** Embedding ran 4.1 times slower than on the RTX 4080 alone.
- **`GGML_VK_VISIBLE_DEVICES`.** Its indices follow Vulkan's raw enumeration, which lists the same GPU twice and differs from llama.cpp's list.
- **The first GPU, as ADR 0049 considered.** On the measured laptop it is the GTX 1060, the slowest choice.
- **Benchmarking each GPU when a model loads.** Every process would load the model once per GPU first. On the measured laptop memory already picks the fastest card.

## Consequences

- On the measured laptop, embedding is 4.1 times faster than llama.cpp's default split, and the LLM's prompts, about 1,500 tokens per tagging call, run on the RTX 4080.
- A machine with two equal GPUs uses one of them, and its other GPU stays free.
- A card with more memory but less speed would be chosen; `model.gpu_device` overrides it.
- A model that fits only across several GPUs together no longer loads on them; it runs on the CPU (ADR 0086), unless `model.gpu_device` names a GPU that holds it.

## Invalidation conditions

- If ggml's registry exposes a GPU's speed or compute capability, choose by that instead of memory.
- If a supported model outgrows every single GPU while the GPUs together could hold it, revisit splitting for that model.
- If llama-cpp-python wraps ggml's device registry, use its API instead of `ctypes`.

## Validation

- Natively on the measured Windows laptop with the Vulkan wheel: `llama_runtime.gpus()` listed the GTX 1060 (6,245 MB) at index 0 and the RTX 4080 (11,997 MB) at index 1, leaving out the integrated GPU and the CPU. `auto` chose index 1, and Imprint's embeddings ran at 5,525 tokens/s on 160 chunks.
- `tests/test_llama_runtime.py`: `auto` takes the GPU with the most memory, an index names a GPU or none, the whole model goes on the chosen GPU, no layers or no GPU means the CPU, and the CPU wheel lists no GPU.
- Not verified: the local LLM's tagging speed on the chosen GPU, machines with only an integrated GPU, and AMD GPUs.
