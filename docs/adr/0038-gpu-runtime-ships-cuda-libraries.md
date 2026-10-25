---
title: ADR 0038 — GPU runtime ships its CUDA libraries
description: The GPU profile installs CUDA 13 and cuDNN from NVIDIA wheels in its hashed lock, gated on the driver's supported CUDA version.
---

- Status: Accepted; the `[cuda,cudnn]` library set, the `nvidia-smi` gate, the Identity check and the profile-specific verdict superseded by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)
- Date: 2026-10-24
- Related: ADR 0007, ADR 0032

## Context

After ADR 0032, a fresh Windows install with the GPU profile on an RTX 4080 (driver 581.80) still ran embeddings on CPU. The CUDA smoke test failed with `onnxruntime_providers_cuda.dll ... depends on "cublasLt64_13.dll" which is missing`.

The locked `onnxruntime-gpu` 1.31.0 links CUDA 13, but nothing installed CUDA 13. Setup expected a system CUDA toolkit, and its only repair added CUDA 12 wheels, unpinned. `_preload_cuda_libs` searched only Linux library paths, for the CUDA 12 layout.

Three smaller problems came with it:

- The setup log was unreadable: ONNX Runtime writes its native log to stderr in UTF-16 on Windows. Setup read stdout and stderr together, so it missed the smoke script's `ERR:` line and logged the UTF-16 text, NUL bytes included.
- The CUDA verdict in `data/gpu_state.json` was keyed on GPU, driver and Python only. A runtime that fixed the problem would never be retried.
- The verdict was also sticky for an explicit `gpu` profile, so even a repaired machine skipped the check.

ONNX Runtime 1.31 declares `[cuda]` and `[cudnn]` extras: NVIDIA's CUDA 13 runtime, cuBLAS, cuFFT, cuRAND, NVRTC and cuDNN 9 wheels. It also provides `onnxruntime.preload_dlls()`, which loads them from those wheels on Windows and Linux, including CUDA 13's consolidated `nvidia/cu13` layout. With these, the only system requirement is an NVIDIA driver that supports the CUDA major; the driver reports this as `nvidia-smi`'s "CUDA Version".

## Decision

`requirements/gpu.txt` requires `onnxruntime-gpu[cuda,cudnn]`. The NVIDIA wheels are therefore pinned and hashed in `gpu.lock.txt` like every other GPU dependency. Every pin must have `win_amd64` and manylinux wheels; Linux-only NVIDIA patch releases are excluded in `gpu.txt`.

Setup compares the driver's CUDA major (from `nvidia-smi`'s header) with the major of the lock's `nvidia-cuda-runtime` pin. When the driver is known to be older, both `gpu` and `auto` install the CPU runtime and name the CUDA version to update the driver to. The lock stays the single source of the requirement. When either version is unknown (no `nvidia-smi`, as in some containers, or an unlocked checkout), setup does not gate on it and the smoke test decides.

Embeddings and the smoke test load the CUDA libraries with `preload_dlls()`. Embeddings send its printed output to stderr, because stdout carries the MCP protocol. The smoke test reads only stdout and reports the libraries `preload_dlls()` could not load.

The CUDA verdict records the GPU runtime it was measured on (a digest of the GPU requirements file) and is compared exactly. A verdict from another GPU lock, or one that predates this field, is retried once. `auto` uses the recorded verdict to stay on CPU; an explicit `gpu` profile smoke-tests on every run. The CUDA 12 repair install and `cudaRuntimeMissing` are removed.

## Alternatives

- **Detect the installed CUDA toolkit and pick a matching build.** The toolkit is irrelevant once the libraries come from wheels, and most machines don't have one.
- **Maintain separate CUDA 12 and CUDA 13 GPU locks.** ONNX Runtime 1.31's PyPI build targets CUDA 13. A CUDA 12 build would need another package index or an older runtime, and a second GPU environment to maintain. CUDA 12 drivers predate R580; updating the driver is the supported path.
- **Keep the repair step but switch it to CUDA 13 wheels.** That still installs unpinned packages after the fact, from a guess based on error text.
- **Install the GPU runtime on old drivers anyway, since it also runs on CPU.** That downloads about 1.3 GB that cannot help.

## Consequences

The GPU profile downloads about 1.3 GB of NVIDIA libraries; the measured Windows venv is 1.86 GB. Switching to the CPU runtime leaves them installed, so switching back reuses them. Drivers older than R580 get the CPU runtime with an explicit message.

Existing hosts with a pre-0038 "broken" verdict retry CUDA once on the next setup or update. Refreshing the GPU lock retries CUDA once on every GPU host.

Native ONNX Runtime log text no longer reaches setup's log. The smoke test's reason comes from `preload_dlls()` and the session's providers instead.

The llama-cpp-python CUDA build still needs a system toolkit (`nvcc`) and keeps its own verdict.

## Validation

- **Windows, natively** (RTX 4080 Laptop, driver 581.80): `gpu.lock.txt` installed with `--require-hashes` into a fresh venv in 39 s. The setup smoke script printed `OK`, and a 512×512 MatMul ran on `CUDAExecutionProvider` with onnxruntime-gpu 1.31.0.
- **Linux (WSL2), same GPU,** with the built `imprint bootstrap` in a throwaway install tree:
  - `auto` with a pre-0038 "broken" verdict retried, installed the GPU runtime and passed the CUDA check, recording `ok` with the runtime digest.
  - With a fake `nvidia-smi` reporting CUDA 12.8, `auto` and `gpu` both installed the CPU runtime with the driver message.
  - With the real driver again, `auto` switched back to the GPU runtime.
- **Unit tests:**
  - `cmd/setup_test.go` covers the driver header and the lock's CUDA major.
  - `internal/gpustate` covers exact runtime matching.
  - `tests/test_models.py` checks that `preload_dlls()` output never reaches stdout.

Not verified: Linux aarch64 GPU hosts, and drivers older than R580 on real hardware.
