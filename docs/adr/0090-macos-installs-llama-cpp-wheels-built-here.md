---
title: ADR 0090 — macOS installs llama.cpp wheels built by this repository
description: A manually run workflow builds llama-cpp-python's macOS wheels from the hash-locked source and publishes them as a prerelease of this repository, and the locks pin them by URL and sha256, so no Mac needs a compiler.
---

- Status: Accepted
- Date: 2026-10-27
- Amends:
  - [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) — macOS installs a prebuilt wheel instead of the source distribution
  - [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) — macOS no longer needs the Xcode Command Line Tools for the binding
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md), [ADR 0085](0085-main-only-fast-forwards-to-dev.md)

## Context

ADR 0086 made `llama-cpp-python` part of every installation, because embeddings run on it. PyPI publishes it only as source. Windows and Linux install upstream's prebuilt wheels (ADR 0035), but every Mac built the binding at install time, which needs the Xcode Command Line Tools. The owner asked for llama.cpp to be built while Imprint is built and packaged, "to avoid having the user do anything else".

Upstream's macOS assets for 0.3.36 do not cover this. Apple Silicon has a CPU-only wheel and a Metal wheel whose archive fails its CRC check (ADR 0035); Intel Macs have none.

The binding decides most of its macOS build itself. Its `CMakeLists.txt` sets `GGML_METAL=ON` and `GGML_METAL_EMBED_LIBRARY=ON` with `FORCE` on both architectures, and on x86_64 forces AVX, AVX2, FMA and F16C off. A first build that passed `-DGGML_METAL=OFF` for Intel still produced a Metal build, and its CPU-only check failed ([run 40474768390](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40474768390)). Every source install on a Mac has therefore always had Metal.

## Decision

`.github/workflows/llama-wheels.yml`, run manually with the binding's version:

- reads the source distribution's pin and hash from `requirements/llm.lock.txt`;
- builds one wheel per architecture with `pip wheel --require-hashes`, on the self-hosted Apple Silicon runner and GitHub's hosted Intel runner, for macOS 13 and with `GGML_NATIVE=OFF`, so the build machine's CPU features stay out of the wheel;
- fails unless each archive passes its CRC check and loads in a fresh venv with GPU offload;
- publishes both wheels and their `SHA256SUMS` as the prerelease `llama-cpp-python-v<version>` of this repository.

`requirements/llm.txt` and `llm-gpu.txt` pin these wheels by URL for `darwin` arm64 and x86_64, and the locks record their sha256, as for upstream's wheels. Both profiles install the same macOS wheel: the CPU and GPU builds differ only on Windows and Linux.

Everything that reads this repository's releases takes only Imprint's tags, `vX.Y.Z` and `vX.Y.Z-dev.N`. The installers and `imprint update` already did; the website's release list now does too.

## Rejected alternatives

- **Keep building at install time.** Every Mac then needs the Xcode Command Line Tools, which the owner asked to avoid. An install also fails offline, or wherever Apple's tools cannot be installed.
- **Upstream's macOS wheels.** The Metal wheel's archive is corrupt, the CPU wheel loses Metal on Apple Silicon, and there is nothing for Intel (ADR 0035).
- **Build the wheels in every release.** The builds are not bit-for-bit reproducible: two Intel builds of the same source were 25,250,250 and 25,250,278 bytes. The lock must pin one artifact, so the wheels are built once per binding version.
- **Bundle the wheel in the macOS package.** Only the `.pkg` would carry it. `install.sh`, `imprint update` and repairs install from the locks, so they would still compile.
- **CPU-only wheels for Intel Macs.** The binding forces Metal on whatever the arguments say. A CPU-only wheel would need a patched build file, and would differ from what Intel Macs have always built.

## Consequences

- No Mac needs a compiler for the binding. Installs skip the build: 49 s on the Apple Silicon runner and 3 min 12 s on the hosted Intel runner.
- Intel Macs get Metal, as their source builds did. `model.device` auto places models on the GPU with the most memory (ADR 0087). Whether Metal beats the CPU on an Intel Mac is not measured.
- Changing the binding's version takes three steps: lock the new source, run the workflow, then pin its wheels and lock again ([building](../building.md)).
- The wheels are only as trustworthy as the two runners that built them. The locks pin the published files, so a replaced asset fails installation instead of being installed.
- CI's macOS jobs install the wheels instead of compiling the binding.

## Invalidation conditions

- If upstream publishes intact Metal wheels with digests for both macOS architectures, pin those and retire the workflow.
- If GitHub retires its hosted Intel macOS runners, Intel wheels need another builder, or Intel Macs lose support.
- If the binding stops forcing Metal on Intel, decide whether Intel Macs should offload.
- If a binding version cannot be built on these runners, its macOS installs would need a compiler again; keep the previous version until the wheels build.

## Validation

- [Run 40476000399](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/40476000399) built and published both wheels. Each passed its CRC check, installed into a fresh venv and reported GPU offload.
- Downloaded again: both files match `SHA256SUMS` and GitHub's asset digests, and the locks record the same values (arm64 `ac70c140…57b36ec`, x86_64 `8364da57…de89ec6`). Their layout matches upstream's wheels: `llama_cpp/`, plus the top-level `include/` and library directory a source install also writes.
- The locks were recompiled with the pinned uv 0.5.11. Only the binding's entries changed.
- `tests/test_onnx_runtime.py`, `tests/test_release_validation.py`, and the setup and llama.cpp install tests in `cmd`, pass.
- Not verified: a Mac installing through the `.pkg` or `imprint update`, and Metal on an Intel Mac with a real GPU.
