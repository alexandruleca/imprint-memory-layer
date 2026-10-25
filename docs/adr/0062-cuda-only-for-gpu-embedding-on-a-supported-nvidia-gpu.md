---
title: ADR 0062 — CUDA only for GPU embedding on a supported NVIDIA GPU
description: The embedding runtime follows model.device and the machine, not the install profile; the CUDA build carries only the NVIDIA libraries its provider loads, and switching builds removes what the old one used.
---

- Status: Accepted
- Date: 2026-10-25
- Amends:
  - [ADR 0032](0032-one-onnx-runtime-per-environment.md) — runtime selection
  - [ADR 0038](0038-gpu-runtime-ships-cuda-libraries.md) — which NVIDIA wheels, the driver gate, the CUDA check and its verdict
  - [ADR 0045](0045-setting-choices-and-gpu-auto-select.md) — the check now runs on the selected GPU
- Related: [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) and [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md), which this record builds on
- Restructured: 2026-10-25

## Context

The install profile decided the ONNX Runtime build: `gpu`, and `auto` on a machine where `nvidia-smi` found a GPU, installed `onnxruntime-gpu[cuda,cudnn]` (ADR 0032, 0038). `model.device` defaults to `cpu` (ADR 0010), and the default model, Qwen3-Embedding-0.6B's int8 export, ran slower on CUDA than on the CPU (RTX 4080 Laptop: 2.4 s against 1.4 s per 4,000-character exchange). So by default the GPU build was dead weight. On the reporting Windows machine (profile `gpu`, `model.device=cpu`) the venv held 1.6 GB of NVIDIA libraries and 228 MB of `onnxruntime-gpu`, which uv's cache in the install tree held a second link to. The profile's other meaning, the local LLM's Vulkan build (ADR 0049), helps any NVIDIA, AMD or Intel GPU and needs no CUDA at all.

Five measurements on that machine (RTX 4080 Laptop at CUDA index 0, GTX 1060 at index 1, driver 581.80) and on Linux under WSL2 with the same GPUs shaped this decision.

**What the CUDA execution provider loads.** In ONNX Runtime 1.31:

- `onnxruntime_providers_cuda.dll` imports only cuBLAS and cuBLASLt. It loads the CUDA runtime, cuDNN, NVRTC and cuFFT by name when it needs them.
- `libonnxruntime_providers_cuda.so` links cuBLAS, cuBLASLt, cuRAND and the CUDA runtime (`readelf -d`).
- `preload_dlls()` treats cuFFT and NVRTC as optional, and on Windows does not list cuRAND.

Removing wheels one at a time and running real inference on CUDA gave these results:

| Missing | Windows | Linux |
| --- | --- | --- |
| cuFFT, nvJitLink | Both models run on CUDA | Both models run on CUDA |
| cuRAND | Both models run on CUDA | The provider fails to load and the session falls back to CPU |
| cuDNN | The session starts, then the first attention node fails (`RequireCudnnHandle`) | Not tried |

The models were the default model and BGE-M3 fp16 (`Xenova/bge-m3`, `onnx/model_fp16.onnx`), with batches of 1 and 4 of up to 1,240 tokens. NVRTC cannot be dropped: the cuBLAS wheel requires it.

**The CUDA check passed without cuDNN.** Setup's check ran an Identity node, which needs neither cuDNN nor cuBLAS. It also always used device 0, which ADR 0045 left as a known gap.

**uv links the venv to its cache.** `uv pip install` hard-links each installed file to the unpacked wheel in `.uv-cache`. Uninstalling a package therefore frees no space until its cache entry is removed too. With a cache inside the install tree, `uv cache clean <package>` removes both the index entry and the unpacked files.

**Windows lets setup replace a loaded runtime, with one limit.** On Windows 11 25H2 (NTFS) a process kept ONNX Runtime loaded, including the CUDA provider and the cuDNN and cuBLAS DLLs, while uv:

- uninstalled the package;
- installed the other build over it;
- reinstalled it.

Every step succeeded, and the process kept running inferences on CUDA afterwards. Deleting a loaded DLL succeeds as long as another hard link to it remains, here uv's cache. Deleting the last link fails with "Access is denied". `uv cache clean` then stops at that file after it has already dropped the index entry, and `uv cache prune` deletes the leftover once the process has exited. So the premise that setup must not run while Imprint holds the runtime (docs/queue.md, ADR 0032's alternatives) does not hold on this build. Only freeing those files waits.

**Packages from older locks linger.** Removing only what the current GPU lock names would leave cuFFT and nvJitLink, about 315 MB, on every installation from ADR 0038's lock. Nothing in the new lock names them.

## Decision

**The rule.** `imprint/gpu_runtime.py` decides the runtime, using the standard library only, so setup can ask before installing anything. It chooses the GPU build only when all of these hold, and the CPU build otherwise:

- `model.device` is `gpu` or `auto`;
- the platform is one the GPU lock has wheels for (Windows x64, Linux x64, Linux arm64);
- the driver's CUDA version (`cuDriverGetVersion`) is at least the lock's CUDA major;
- the GPU `model.gpu_device` selects has the compute capability that major needs: `auto` takes the first such GPU, and an explicit index must name one (`cuda_devices.MIN_COMPUTE`);
- no CUDA failure is recorded for this environment.

Machines with only AMD or Intel GPUs therefore never get a CUDA package; their LLM keeps the Vulkan build. `cuda_devices.runtime_major()` now reads the CUDA major from the GPU requirements instead of the installed wheel, so Settings marks an unsupported GPU before anything is installed. The decision and its reason, as one sentence fragment, feed:

- setup's output: `Embeddings run on the GPU: …`, or a warning for `gpu`;
- `model.device`'s choices in Settings: `gpu` is unavailable with the reason, and `auto` says `CPU here: …` or names the GPU and the download;
- `imprint config set`, which prints the chosen value's detail or reason, and for `model.device` and `model.gpu_device` how to apply a runtime change;
- a new `imprint doctor` check, `runtime:embedding-device`.

**What `auto` means now.** For the profile, `cpu` installs the CPU build of llama.cpp, and `gpu` and `auto` both install the GPU build when llama.cpp finds a GPU. The two are therefore the same; both values stay valid for saved `profile.json` files, installers and scripts. For `model.device`, `gpu` and `auto` both ask for GPU embedding and install the same runtime. Their run-time differences are unchanged: `auto` uses CUDA when it is present, `gpu` batches 4, and BGE-M3's `model.file=auto` picks fp16 for `gpu`.

**Only the libraries the provider loads.** `gpu.txt` lists `onnxruntime-gpu` with:

- `nvidia-cuda-runtime`;
- `nvidia-cudnn-cu13`, which brings `nvidia-cublas` and its `nvidia-cuda-nvrtc`;
- `nvidia-curand`, for Linux only;
- `onnx`, for the check.

The `[cuda,cudnn]` extras are no longer used. Their specifiers move with ONNX Runtime's CUDA major (docs/building.md). The lock was recompiled with the pinned uv: cuFFT and nvJitLink left, cuRAND gained the Linux marker, and every other pin and hash is unchanged.

**The CUDA check moves to Python and tests what embeddings need.** `python -m imprint.gpu_runtime check` runs a Conv (cuDNN) and a MatMul (cuBLAS) on the planned GPU and compares the result. It records the verdict in `data/gpu_state.json`, keeping the same keys and fingerprint: GPU name, driver version from NVML, Python version, and a digest of the GPU lock. The verdict now applies to `gpu` and `auto` alike. A failed check installs the CPU build: retrying on every run would download more than 1 GB each time, and a new driver, GPU, Python or lock still retries automatically. `--retry-gpu` passes `--retry` to the plan, which forgets the verdict. `internal/gpustate`, the `nvidia-smi` parsing and the Identity check are removed from Go.

**Switching builds installs first and removes what is unused.** `installRuntime`:

1. Installs the selected requirements first, so uv downloads before it changes the venv.
2. Removes stale ONNX Runtime providers and reinstalls the selected one from the cache (ADR 0032).
3. Uninstalls the distributions that `python -m imprint.onnx_runtime unused <runtime>` names, and runs `uv cache clean` on each one separately.
4. Runs `uv cache prune` at the end of every provisioning run.

`unused` is computed before installing, while the old build still describes itself:

- Both runtimes' requirements are roots, and the walk follows installed metadata, a root's extras included.
- Distributions sharing a package directory with a removed one (`nvidia/`) go with it.
- Nothing another requirements file names, other than the other runtime's, is removed.
- Nothing a remaining distribution requires is removed.

**The dashboard provisions through the queue.** When `PUT` or `DELETE /api/config/model.device` (or `model.gpu_device`) leaves the plan different from the installed runtime, the server queues `imprint bootstrap --non-interactive` and returns the job ID as `provisioning`. It also queues one when a bootstrap job is already running, since that job may have read the configuration before the change. Settings shows a toast that links to the queue. The dispatcher calls back into the serving app after each job: `python -m imprint.api` serves from `__main__`, so importing `imprint.api` from the queue would reach a second copy. After a successful bootstrap that replaced the ONNX Runtime the server loaded, the server restarts through its launcher (exit 75, ADR 0050). MCP servers and other processes switch when they next start. The Commands page still offers no runtime-installing command.

## Rejected alternatives

- **Keep the profile in charge of the runtime.** Every NVIDIA machine on the default settings downloads 1.3 to 1.6 GB it does not use, and the profile would keep conflating the LLM with embeddings.
- **Default GPU machines to GPU embedding.** The default model is slower on CUDA, and its CUDA vectors differ from its CPU ones (lowest cosine 0.91 on natural text).
- **Keep the full `[cuda,cudnn]` extras.** That adds 257 MB of download on Windows and 204 MB on Linux x64 for libraries no supported model loads.
- **Remove only what the current lock names.** cuFFT and nvJitLink would stay on every installation from ADR 0038's lock, and so would every library a future lock drops.
- **`uv pip sync` to an exact environment.** It would remove what users add to the venv, such as kreuzberg and the OCR packages.
- **Stop every Imprint process before swapping.** That disconnects running MCP sessions, and the measurements show a swap does not need it; only the space waits for those processes.
- **`uv cache prune` without cleaning first.** The removed packages' entries are still reachable, so prune keeps them.
- **Install from the dashboard process with uv.** That is a second installer next to setup; ADR 0035 rejected the same thing for the LLM binding.
- **ONNX Runtime's DirectML build for AMD and Intel on Windows.** It is a vendor-neutral GPU path but a third runtime with its own lock, model-compatibility and identity questions. It is left as a follow-up.

## Consequences

- A default installation downloads ONNX Runtime's CPU build only: 15 MB on Windows instead of 1,342 MB, and 24 MB on Linux x64 instead of 1,561 MB, measured from the pinned wheels. An installation from ADR 0038 with `model.device=cpu` frees about 1.8 GB on Windows (2.3 GB on Linux) at its next update or `imprint setup`.
- GPU embedding downloads 1,085 MB on Windows, 1,357 MB on Linux x64 and 1,506 MB on Linux arm64, against 1,342, 1,561 and 1,708 MB before. GPU installations from ADR 0038 lose cuFFT and nvJitLink on their next setup.
- The Windows installer's profile page and the CLI help describe the profile as the local LLM's build.
- On Windows, while a process has an old library loaded, its file stays in uv's cache until a later setup prunes it. Setup says so.
- If uv cannot hard-link, for example on a file system without hard links, deleting a loaded library fails as before (ADR 0032): setup stops and asks for another run. That path was not measured.
- A queued bootstrap that is cancelled mid-install leaves a venv the next setup repairs.
- A bootstrap job reinstalls the LLM overlay and the shell alias like any setup run, and its log ends with bootstrap's own hint to run `imprint setup`.
- The `more than 1 GB` in Settings is deliberately approximate.

## Invalidation conditions

- If the default embedding model runs faster on CUDA than on the CPU, revisit leaving GPU machines on CPU embedding by default.
- If an ONNX Runtime upgrade makes the CUDA provider load a library this set leaves out, re-run the removal measurements and change the set.
- If uv stops hard-linking the venv to its cache, or Windows stops allowing a loaded DLL to be deleted while another link remains, swapping builds under a running process fails as ADR 0032 described.
- If a vendor-neutral GPU path such as DirectML is adopted, left here as a follow-up, GPU embedding is no longer CUDA-only.

## Validation

**Unit tests:**

- `tests/test_gpu_runtime.py` covers:
  - the decision matrix: device × NVIDIA presence, driver, compute capability, explicit and auto GPU, platform, missing requirements and recorded failure;
  - that the CPU device probes no hardware;
  - the installed-runtime and pending messages;
  - the check's verdicts and their fingerprint, and the CLI's `--retry`;
  - the device choices, and `config set` output;
  - the API queueing bootstrap, including deduplication, a running job and a reset;
  - the restart only into another runtime, and the dispatcher's callback.
- `tests/test_onnx_runtime.py` covers `unused`:
  - an ADR 0038 venv moving to CPU;
  - CPU with leftover NVIDIA wheels;
  - a GPU venv with stale cuFFT;
  - moving to GPU;
  - packages a user's distribution requires, which stay;
  - that the shipped lock is trimmed.
- `cmd/setup_test.go` drives `provisionEmbeddingRuntime` with a stateful fake uv, pip and python: migration, steady state, fresh venv, enabling the GPU, a failed check, retry and an unreadable plan.
- Results: the full Python suite (462 tests), `go vet ./...` and `go test ./...` (also the `cmd` tests cross-compiled and run natively on Windows), and the dashboard's `tsc`, `eslint`, production build and full Playwright suite (95 tests, including two new Settings tests) pass.

**Lock:** recompiled with uv 0.5.11. A per-entry diff against the old lock shows only cuFFT and nvJitLink removed and cuRAND's marker added.

**Windows, natively, on a scratch install tree with the cross-compiled CLI:**

- Migration: a venv built from ADR 0038's lock (2,108 MB, NVIDIA 1,613 MB) moved to the CPU build in 40 s, downloading one 15 MB wheel. Meanwhile a process with ONNX Runtime loaded kept working. The venv went to 306 MB and the cache to 381 MB, and 1,773 MB of disk was freed at once. The next setup's prune freed the 25 MB the running process had held.
- With another process holding the CUDA DLLs, the venv swap succeeded and that process's CUDA sessions kept working, while `uv cache clean` could not delete those files until it exited.
- `imprint config set model.device gpu` printed what it installs. `imprint bootstrap` then installed the trimmed GPU build (venv 1,753 MB, NVIDIA 1,239 MB) and passed the new CUDA check on the RTX 4080.
- A real `imprint ui start` server took `DELETE` and then `PUT` of `model.device` through queued bootstrap jobs (10 s and 68 s) and restarted with a new pid each time.
- The trimmed set ran both models on CUDA, and removing cuDNN made the new check fail where the old one would have passed.

**Linux (WSL2):**

- The same migration freed 2.3 GB, with a process holding the old runtime throughout.
- The dashboard flow switched to GPU and back with restarts.
- `CUDA_VISIBLE_DEVICES=` and `model.gpu_device=1` (the GTX 1060) kept the CPU build with the reasons shown in setup, `config set` and `doctor`.
- Both models ran on CUDA with the trimmed set plus cuRAND.

**Not verified:**

- Windows 10, where deleting a loaded library's linked file was not tried;
- Linux arm64 GPU hosts;
- drivers older than R580 on real hardware;
- a real Inno Setup update from a release with the old runtime, as opposed to the scratch tree;
- machines with only AMD or Intel GPUs, where the rule was exercised with the driver hidden;
- EmbeddingGemma on CUDA.
