---
title: ADR 0125 — Integrated GPUs can be chosen beside a discrete one
description: Settings lists integrated GPUs after the discrete ones, and each model loads through llama.cpp's device list, so model.gpu_device can name an integrated GPU even when a discrete GPU exists; auto still prefers the discrete GPU.
---

- Status: Accepted
- Date: 2026-11-04
- Amends: [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) — which GPUs `model.gpu_device` can name, and how a model is put on the chosen GPU
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md)

## Context

The user reported a Windows laptop with an integrated AMD Radeon GPU, 128 GB of system memory and a GTX 1060 in an external enclosure. Settings listed only the GTX 1060, and `auto` ran embeddings and the local LLM on it. ADR 0087 measured the GTX 1060 at 632 tokens/s for embedding, the slowest of the GPUs it measured.

ADR 0087 left integrated GPUs out of the list whenever a discrete GPU exists, as llama.cpp's own list does. A GPU was also put on a model by its position in that list, as `main_gpu`. So an integrated GPU beside a discrete one could not be named at all.

Two facts about the pinned binding, llama-cpp-python 0.3.36, shape the fix:

- **llama.cpp takes an explicit device list.** `llama_model_params.devices`, a NULL-terminated list of ggml devices, replaces llama.cpp's default list, and `main_gpu` then indexes it. Measured on an Apple M3 with the Metal wheel: with `split_mode` NONE and `main_gpu` 1, the embedding model failed to load with the default list (one GPU) and loaded with a two-entry list naming the M3 twice.
- **The binding's `Llama` does not set it.** The struct has the field, but `Llama.__init__` takes no such argument. It builds its params from `llama_cpp.llama_cpp.llama_model_default_params()` and passes them to the model loader.

## Decision

- `llama_runtime.gpus()` lists the discrete GPUs in registry order, then the integrated ones, each flagged as integrated. Discrete GPUs keep the indices ADR 0087 gave them.
- `auto` is unchanged: the discrete GPU with the most memory, and an integrated GPU only when there is no discrete one. An index can name any listed GPU, integrated ones included. Settings marks integrated GPUs.
- Every model put on a GPU loads with `devices` set to that one GPU, `split_mode` NONE and `main_gpu` 0. While `Llama` loads the model, `_offload_to` replaces the binding's `llama_model_default_params` with one that sets `devices`. The existing construction lock serializes loads, and llama.cpp copies the list during the load.

## Rejected alternatives

- **Ranking integrated GPUs by memory in `auto`.** On ADR 0087's laptop the integrated Intel GPU reported 16 GB and the RTX 4080 12 GB, so memory would have picked the integrated GPU. An integrated GPU's memory is a share of system memory and says nothing about speed.
- **`GGML_VK_VISIBLE_DEVICES`.** ADR 0087 rejected it: its indices follow Vulkan's raw enumeration, which listed the same GPU twice on the measured laptop.
- **`main_gpu` over llama.cpp's default list.** That list has no integrated GPU when a discrete one exists, so no index reaches it.
- **The device list for integrated GPUs only, `main_gpu` for discrete ones.** That keeps two ways of placing a model, and the discrete one still assumes llama.cpp's list order matches the registry's.

## Consequences

- On the reported laptop, Settings lists the integrated Radeon after the GTX 1060, and `model.gpu_device` set to its index runs embeddings and the local LLM on it. `auto` still picks the GTX 1060.
- Saved indices keep naming the same GPUs, because discrete GPUs come first. On a machine with only integrated GPUs, the list and indices are the same as before.
- A model on an integrated GPU uses system memory.
- The fix relies on `Llama.__init__` building its params from the binding's `llama_model_default_params`. A test checks this and the struct's `devices` field, so a binding update that changes either fails the test.
- Known gap: the integrated Radeon's speed against the GTX 1060 has not been measured.

## Invalidation conditions

- If llama-cpp-python's `Llama` accepts a device list, pass it directly and drop the replaced default params.
- If ggml's registry exposes a GPU's speed or compute capability, `auto` should rank discrete and integrated GPUs by it (as ADR 0087's own condition says).
- If measurements on machines that have both show integrated GPUs outrunning the discrete ones, reconsider `auto`'s preference for discrete GPUs.

## Validation

- `tests/test_llama_runtime.py`: `auto` prefers a discrete GPU and takes an integrated one only when there is no discrete one; an index names an integrated GPU beside a discrete one; placement passes the chosen device with `main_gpu` 0; the NULL-terminated list is set only while a GPU load runs, and the default params are restored after; the binding's `Llama` builds its params from the defaults, and the struct has `devices`; the installed build lists discrete GPUs first.
- macOS on an Apple M3, Metal wheel 0.3.36: the default-list and two-entry-list loads above; the embedding model loaded on the M3 through `load()` with the device list, and its embedding matched the CPU's at cosine 0.9997.
- Not verified: Windows with the Vulkan wheel on the reported laptop, an AMD integrated GPU, and a re-run on ADR 0087's RTX 4080 and GTX 1060 laptop.
