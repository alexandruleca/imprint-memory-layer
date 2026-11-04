---
title: ADR 0126 — CUDA only for an NVIDIA GPU CUDA 13 supports
description: Setup tries ONNX Runtime's CUDA build only when nvidia-smi lists an NVIDIA GPU of compute capability 7.5 or newer, the oldest CUDA 13 supports, instead of for any NVIDIA GPU.
---

- Status: Accepted
- Date: 2026-11-04
- Amends: [ADR 0122](0122-the-sending-device-filters-on-its-gpu.md) — which NVIDIA GPUs make setup try the CUDA build
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)

## Context

ADR 0122's setup plan tries ONNX Runtime's CUDA build whenever `nvidia-smi -L` lists an NVIDIA GPU. The build brings NVIDIA's CUDA 13 and cuDNN wheels, about 2.5 GB installed ([installation](../installation.md)).

CUDA 13 has no code for GPUs older than compute capability 7.5:

- ADR 0049 measured llama.cpp's CUDA 13 build abort with all GPUs visible, because it had no code for the GTX 1060 (compute capability 6.1).
- ADR 0062 gated GPU embedding on that minimum (`MIN_COMPUTE = {13: (7, 5)}` in `imprint/cuda_devices.py`). The gate went away with that module when embeddings moved to llama.cpp (ADR 0086), and ADR 0122 did not bring it back.

The user reported that setup installed the CUDA build on a Windows laptop whose only NVIDIA GPU is a GTX 1060 in an external enclosure.

## Decision

`onnx_runtime.nvidia_gpu()` runs `nvidia-smi --query-gpu=index,name,compute_cap --format=csv,noheader`. It returns the first GPU with compute capability 7.5 or newer, and nothing when there is none, so the plan then has no CUDA step. A machine without such a GPU goes on to the next build ADR 0122 lists: DirectML on Windows, then the CPU build.

## Rejected alternatives

- **Leaving it to setup's check.** The check runs only after the CUDA wheels are downloaded and installed, and then setup has to remove them again.
- **Reading compute capability through the CUDA driver API, as ADR 0062's module did.** That module was deleted with ADR 0086. `nvidia-smi` was already called here, and its query reports the same number without loading the driver library.

## Consequences

- A machine whose NVIDIA GPUs are all older than Turing (GTX 10-series and earlier) no longer downloads the CUDA build. On Windows it tries DirectML, which ADR 0122 found cannot run the filter's expert layers on the GPU, so the filter will likely stay on the CPU.
- A machine with both kinds, such as an RTX 4080 beside a GTX 1060, still tries CUDA. Which GPU the session runs on is unchanged by this record.
- A driver too old to know the `compute_cap` field prints an error, so CUDA is not tried. Such a driver is also too old for CUDA 13.

## Invalidation conditions

- If the CUDA lock moves to another CUDA major, set the minimum to that major's oldest supported compute capability.
- If `nvidia-smi` stops reporting `compute_cap`, read it another way, such as the driver API.

## Validation

- `tests/test_onnx_runtime.py`: with a GTX 1060 (6.1) and an RTX 4080 (8.9) listed, the 4080 is chosen; a GTX 1060 alone yields no CUDA step; a driver that rejects the field yields none.
- Not verified: real `nvidia-smi` output on Windows, and setup end to end on the reported laptop.
