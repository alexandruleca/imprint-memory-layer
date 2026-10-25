---
title: ADR 0032 — One ONNX Runtime per environment
description: Each install profile resolves to base requirements plus exactly one ONNX Runtime build, verified from package records.
---

- Status: Accepted; selecting the runtime from the profile superseded by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)
- Date: 2026-10-24
- Related: ADR 0004, ADR 0007, ADR 0012

## Context

A Windows GPU install failed every ingest with `module 'onnxruntime' has no attribute 'InferenceSession'`. Its venv held `onnxruntime_gpu-1.31.0.dist-info`, while `site-packages/onnxruntime/` contained only `capi/`.

`onnxruntime` and `onnxruntime-gpu` are separate distributions that write the same `onnxruntime` package directory. The CPU build sat in the base lock, so every setup run on a GPU venv did three things:

1. Installed the CPU build over the GPU files.
2. Uninstalled it, which deleted every file in its RECORD, including the files both builds share.
3. Asked uv to install the GPU build, which uv reported as already installed ("Audited").

The installer runs backend setup twice per install (`bootstrap`, then `setup`), and repair, update and `imprint profile set` run it again. Switching profiles damaged the venv the same way. With uv 0.5.11 this sequence reproduces the exact state above.

The repair path in `ensureOrtGPU` never ran, because a sticky "broken" CUDA verdict returned first. That verdict was itself false. The smoke-test model took the `onnx` package's newest IR version (14), which ONNX Runtime 1.31 rejects (it accepts up to 13), so the test failed on every host.

A check of distribution integrity cannot use `importlib.metadata.Distribution.files` either: on Python 3.12 it leaves out missing files.

## Decision

`requirements/base.txt` no longer lists ONNX Runtime. `cpu.txt` holds `onnxruntime` (keeping the Intel macOS 1.23.2 pin) and `gpu.txt` holds `onnxruntime-gpu` plus `onnx` for the smoke test. Each has a hashed lock compiled against `base.lock.txt`. A runtime is base plus one overlay, installed in a single hashed resolution.

Setup selects the runtime from the profile:

- `cpu` uses the CPU runtime.
- `gpu` uses the GPU runtime.
- `auto` uses the GPU runtime when `nvidia-smi` reports a GPU and no CUDA failure is recorded for this host, and the CPU runtime otherwise.

Before installing, `imprint.onnx_runtime` reads every installed provider of the `onnxruntime` package and checks that each RECORD's files exist; it does not import ONNX Runtime. Setup uninstalls every other provider, and the selected one when its files are incomplete or when any other provider is removed. A healthy environment is left untouched. Setup then requires `onnxruntime.InferenceSession` to load and fails loudly if it does not. `imprint doctor` reports the same state.

The CUDA smoke test declares IR version 8 and requires the session to actually use `CUDAExecutionProvider`. It never swaps packages. After a failed check, `auto` installs the CPU runtime, while an explicit `gpu` profile keeps `onnxruntime-gpu`, which also runs on CPU. Every ONNX Runtime install comes from a hashed lock.

## Alternatives

- **Keep the overlay with uninstall-then-install.** This is the defect itself: it is not convergent.
- **Reinstall the selected build on every run.** Simple and convergent, but every run churns, and on Windows the uninstall fails while Imprint holds the runtime's DLLs open.
- **Detect damage by importing.** The previous `hasattr` stub checks missed partial damage and venvs holding both builds, and they ran only on some paths.
- **One complete lock per runtime, duplicating base.** This gives the same environment as base plus one overlay, at the cost of maintaining two lists.
- **Ship `onnxruntime-gpu` everywhere.** The download is large, and there are no macOS wheels.

## Consequences

The base lock lost ONNX Runtime and its exclusive dependencies; these moved unchanged, with identical pins, markers and hashes, into `cpu.lock.txt`. CI, the container image and benchmark instructions install `base.lock.txt` with `cpu.lock.txt`.

Damaged venvs are repaired by the next bootstrap: installer repair, update or `imprint bootstrap --non-interactive`. If Imprint holds the runtime open, removing a provider fails, and setup asks the user to stop Imprint and retry.

Hosts that recorded the false CUDA verdict keep it until `imprint setup --retry-gpu` or a driver/GPU/Python change. With an explicit `gpu` profile they still run on CPU.

This change does not make CUDA work where it did not before. The locked `onnxruntime-gpu` 1.31.0 Linux wheel links CUDA 13 (`libcublasLt.so.13`, `libcudart.so.13`). The cu12 wheel retry and `_preload_cuda_libs` target CUDA 12, and the preload only recognises Linux library names. Hosts without a matching system CUDA runtime therefore fail the CUDA check and run embeddings on CPU. The retry wheels also remain unpinned, as ADR 0007 notes.

## Validation

- Reproduced the corruption with uv 0.5.11 and the locked wheels on Linux x64: base install, then uninstall, leaves only `onnxruntime/capi/`.
- Reproduced the smoke-test failure: an IR 14 model fails and an IR 7 model loads on ONNX Runtime 1.31.0.
- Ran the built `imprint bootstrap` against a scratch install tree with real uv and wheels, using a fake `nvidia-smi` and a recorded failure for that fingerprint. Each case ended with one complete provider and a loadable `InferenceSession`:
  - fresh CPU install
  - the Windows-damaged GPU venv
  - an unchanged rerun ("Audited", nothing removed)
  - `auto` to CPU
  - CPU to GPU
  - both builds installed
  - `auto` without a GPU
  - a damaged CPU build
- `tests/test_onnx_runtime.py` fails against the `Distribution.files` implementation and passes against the RECORD reader.
- `cmd/setup_test.go` covers the uninstall decision and the combined hashed install arguments.
- pip 24.3 accepts the repeated identical hashed pins in base and overlay locks.

Not yet verified: native Windows and macOS bootstrap with the new layout, and a real CUDA session on any host.
