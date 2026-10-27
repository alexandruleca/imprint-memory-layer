---
title: ADR 0035 — Prebuilt llama.cpp wheels and first-chat provisioning
description: Install llama-cpp-python from upstream's hash-pinned prebuilt wheels and let the first local chat install the binding and download the model.
---

- Status: Amended
- Date: 2026-10-24
- Amended by:
  - [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) — the GPU profile's CUDA rebuild is replaced by upstream's hash-locked Vulkan wheels
  - [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) — the binding is part of every installation; the first local chat only downloads the model
  - [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) — macOS installs wheels this repository builds instead of the source distribution
- Related: [ADR 0012](0012-native-package-runtime-and-silent-installs.md), [ADR 0013](0013-remediate-security-scan-dependencies.md), [ADR 0032](0032-one-onnx-runtime-per-environment.md)
- Restructured: 2026-10-25

## Context

The dashboard chat's `local` provider runs the helper GGUF in-process through `llama-cpp-python`. That binding is optional (`--with-llm`, `imprint profile add-llm`), and the chat answered every message with "llama-cpp-python not installed … Run: imprint profile add-llm" until it was installed.

On a Windows install that had opted in (`profile.json` recorded `with_llm: true`), the binding was still missing. The first-run log shows why: PyPI publishes `llama-cpp-python` 0.3.36 only as a source distribution, so `uv pip install` compiled llama.cpp with CMake. The machine had no C/C++ compiler (`CMAKE_C_COMPILER not set`), and `installLlmReqs` downgraded the failure to a warning. A first-chat install that ran the same command would fail the same way on most Windows machines.

The binding's maintainer also publishes prebuilt wheels as GitHub release assets, with sha256 digests:

- CPU wheels for `win_amd64`, `manylinux_2_17` x86_64/aarch64, musllinux and macOS arm64
- a Metal wheel for macOS arm64, whose archive fails CRC checks (`zipfile.testzip`)

Two more facts shape the design:

- **`profile add-llm` reruns `setupBackend`.** That re-syncs the ONNX Runtime too, which replaces DLLs that the running dashboard and MCP servers hold open on Windows.
- **The chat process detected the binding once, at import.** Even a successful install was not usable until the dashboard restarted.

## Decision

`requirements/llm.txt` pins `llama-cpp-python` 0.3.36 per platform with environment markers:

- Windows x64 and Linux x64/arm64 (glibc) use upstream's prebuilt CPU wheel through direct URLs. The universal lock records each wheel's sha256, matching the digest GitHub publishes.
- Every other platform keeps the PyPI source distribution, with its existing hash. On macOS that build enables Metal, so Apple Silicon keeps GPU offload; the corrupt Metal wheel is not used.

`--require-hashes` installs therefore never compile on Windows or Linux x64/arm64. The GPU profile's CUDA rebuild (`ensureLlamaCppGPU`) is unchanged. *ADR 0049 later replaced that rebuild with upstream's hash-locked Vulkan wheels.*

`imprint profile add-llm` installs only the LLM overlay, the same steps `setupBackend` runs for `--with-llm`:

- the locked requirements
- the CUDA rebuild on gpu/auto profiles

It then verifies `import llama_cpp` and records `with_llm` in `profile.json`. It fails with the install output when the binding still does not import. It requires an environment created by setup.

The first local chat provisions what is missing:

- The binding is imported on demand, retrying after a failure, so one installed while the dashboard runs is used without a restart.
- When the binding or the default model is missing, one process-wide job runs `imprint profile add-llm` and then downloads the model. Each chat stream that needs it follows the job's events: `install_*`, then `download_*`, then `load_start` before the model loads.
- A closed stream does not interrupt the job, because stopping uv mid-install would leave a half-written environment.
- A failed job ends that chat with the job's last output lines. The next chat starts a new job.

`/api/chat/status` reports a missing binding as `installed: false` without an error.

Two related stream changes support the dashboard's chat:

- **No markup in tokens.** Tool-call markup never reaches `token` events: the stream withholds text from the `<tool_call>` tag on, and holds back a trailing partial tag until it resolves.
- **Stopped replies are kept.** When the client closes the stream before the reply completes, the API stores the streamed text as the assistant's message.

The Docker image installs the LLM lock at build time. Its runtime user cannot write the image's venv, so a first-chat install could not run there.

## Rejected alternatives

- **Install the MSVC Build Tools on demand.** This is several gigabytes, needs elevation, and fails offline or on locked-down machines.
- **Upstream's PEP 503 or find-links page.** The page carries no hashes, so uv 0.5.11 recorded only the one wheel it downloaded (the riscv64 wheel), and `--require-hashes` installs failed on every other platform.
- **Use the Metal wheel on Apple Silicon.** Its archive is corrupt. A CPU-only wheel would also lose Metal acceleration compared with the source build.
- **Run `llama-server` binaries behind the OpenAI-compatible provider.** That changes the tagger and summaries as well, and adds a managed local service. ADR 0012 and local-models.md deliberately avoid starting new services.
- **Queue the install as a dashboard job.** The command queue is single-slot, so chat would wait behind a long ingest. Queue jobs are also tied to the CLI's ingest lock semantics.
- **Install from Python (`uv pip install`) in the chat module.** That is a second implementation of the installer's uv scoping, lock selection and CUDA handling.

## Consequences

- Windows and Linux installs of the binding no longer need a compiler. They download about 7 MB (Windows) or 25 MB (Linux) instead of a 73 MB source tree.
- Wheel downloads come from `github.com/abetlen/llama-cpp-python/releases`, from the same maintainer as the PyPI package, pinned by hash. Lock refreshes must compile from scratch (uv keeps existing pins) and should check every wheel's archive.
- Upgrading the binding means updating the version in each URL and in the source-build line together.
- Trivy still sees `llama-cpp-python==0.3.36` and `diskcache` in the lock, so the ADR 0013 advisory stays visible.
- macOS, musl Linux and other platforms still need a toolchain for the binding. If that build fails, first chat shows the build error instead of a generic "not installed".
- `profile add-llm` no longer repairs a broken base environment as a side effect; `imprint setup` does that.
- The provisioning job lives in the dashboard process. A CLI `add-llm` that runs concurrently is not coordinated with it beyond uv's own locking. The model download is already serialized by a file lock.

## Invalidation conditions

- If PyPI publishes hashed prebuilt wheels of the binding for these platforms, the direct GitHub URLs can give way to the index.
- If upstream stops publishing prebuilt wheels or digests for a version Imprint needs, the pin cannot move without compiling; revisit.
- If the macOS Metal wheel passes integrity checks, Apple Silicon could use it instead of the source build.
- If the command queue gains a second lane, the dashboard-owned provisioning job, chosen so chat does not wait behind ingest, could move to the queue.

## Validation

- **Lock:** compiled with the pinned uv 0.5.11. The diff against the previous lock changes only the `llama-cpp-python` lines, and every wheel hash equals GitHub's published digest. The Windows, manylinux x86_64 and aarch64 wheels pass `zipfile.testzip`; the Metal wheel does not.
- **Windows:** a disposable `%TEMP%` venv on the reporting Windows machine, using its installed `uv.exe`, installed the lock with `--require-hashes` without a compiler. It imported `llama_cpp` 0.3.36 (CPU), loaded the user's Qwen3.5-4B GGUF in 1.7 s and generated a reply. The venv was then removed.
- **Linux pip:** plain `pip install --require-hashes -r requirements/llm.lock.txt` (the Docker image's installer) installs and imports the manylinux wheel.
- **Go:** `go build ./...`, `go vet ./cmd` and `go test ./cmd` pass.
- **Python:** `tests/test_chat_stream.py` covers:
  - picking up a binding installed while running
  - first chat installing the binding before answering, with a real subprocess
  - concurrent chats sharing one install
  - a failed install surfacing its output, with the next chat retrying
  - tool-call markup and held-back partial tags
  - storing stopped replies, and completed replies only once
- **Dashboard:** `imprint/ui/tests/chat.spec.ts` covers the setup and progress UI.
- **Not yet verified:** the macOS source build, and `profile add-llm` run by a real dashboard on Windows.
