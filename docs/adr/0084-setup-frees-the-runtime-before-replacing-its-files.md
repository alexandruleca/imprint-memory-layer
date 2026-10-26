---
title: ADR 0084 — Setup frees the runtime before replacing its files
description: Setup holds the queue and runtime locks while it changes the venv, and before it replaces or removes installed packages it restarts the dashboard server and, on Windows, stops every other process running from the runtime; Settings starts bootstrap outside the queue.
---

- Status: Accepted
- Date: 2026-10-26
- Amends:
  - [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) — swapping builds under running processes, provisioning through the queue, and pruning uv's cache
  - [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) — the server's launchers wait while setup changes the runtime, and setup restarts the server
- Related: [ADR 0032](0032-one-onnx-runtime-per-environment.md), [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md), [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md)

## Context

On a Windows installation (RTX 4080 Laptop, bundled uv 0.5.11), switching `model.device` to GPU in Settings failed twice. Both queued `imprint bootstrap` jobs stopped at the first install:

```
error: Failed to install: onnxruntime_gpu-1.31.0-cp312-cp312-win_amd64.whl (onnxruntime-gpu==1.31.0)
  Caused by: failed to rename file from ...\site-packages\.tmpoiihiF\onnxruntime_providers_shared.dll
  to ...\site-packages\onnxruntime\capi\onnxruntime_providers_shared.dll: Access is denied. (os error 5)
```

The installation's own files showed why:

- The dashboard service that queued the jobs (`imprint.desktop_server`) had ONNX Runtime loaded. A venv's `python.exe` on Windows is a launcher that runs the interpreter from `python\` as its child.
- `onnxruntime_providers_shared.dll` had one link. ADR 0062's swap of a loaded runtime works only while uv's cache keeps a second one.
- `first-run.log`: right after a fresh install hard-linked 144 packages, setup's `uv cache prune` printed `Removed 15626 files (438.0MiB)`; the next setup run printed `Removed 96 files (195.1MiB)` after the LLM overlay. The cache's 152 index entries in `wheels-v3` pointed at archives that no longer existed. A scratch install of the same locks with the same uv, and the real `imprint bootstrap`, has exactly 15,626 archive files, and prune keeps all of them; so did prune on a scratch cache inside the installation's directory, and on the installation's cache afterwards. What made prune drop referenced archives during the installer's runs was not reproduced.
- Installing `onnxruntime-gpu` writes over the files of the CPU build (ADR 0032), so uv renamed over the loaded DLL's only link.

ADR 0062 names this as an invalidation condition: if the venv is not hard-linked to the cache, swapping builds under a running process fails. Setup cannot guarantee the second link. Its own maintenance removed it here, uv copies when it cannot link, and a user can clear the cache. The dashboard's flow made it certain to matter, since the job ran under the very process holding the files.

Windows refuses to replace or delete a loaded file's last link; adding files is never blocked. Linux and macOS unlink files that processes keep using.

## Decision

**Setup holds the runtime while it changes the venv.** Before it plans the embedding runtime, setup takes the queue lock (ADR 0064), waiting for a running job and stopping background tagging, and `data/runtime.lock`. `imprint profile add-llm` and `drop-llm` do the same. Concurrent setups take turns, and each plans from the configuration it finds once it holds both.

**Setup frees the runtime before it replaces or removes files, and only then.** A step replaces files when it uninstalls, when uv's dry run (`uv pip install --dry-run`) of an install would uninstall something (an upgrade), or when it installs one ONNX Runtime build over another or repairs one (ADR 0032). An install that only adds packages frees nothing; the first local chat's `add-llm` (ADR 0035) keeps the dashboard running. A dry run that would make no changes also skips the install. Freeing, once per setup run:

- on Windows, stops every process running from the venv or the managed Python except the registered dashboard server and its launcher: MCP servers, the WSL bridge, learners. Politely first, then forcibly, as uninstall does (ADR 0028). AI clients reconnect Imprint afterwards.
- restarts the registered dashboard server through its restart endpoint (ADR 0050) and waits for it, and its launcher, to exit.

**The server's launchers wait for the runtime lock.** `imprint ui`'s supervisor waits before each start of the server; the desktop service waits before any import beyond the standard library. A server restarted by setup loads the venv once setup releases the lock.

**Settings starts `imprint bootstrap` outside the queue.** A change of `model.device` or `model.gpu_device` that needs another runtime starts it detached, like the updater, writing `data/bootstrap.log`; bootstrap restarts the server. A change made while one runs starts another, which waits its turn. `GET /api/config` reports the runtime the configured device still needs and the end of that log; Settings shows it with an Install button (`POST /api/runtime/provision`).

**Setup no longer prunes uv's cache.** With no process holding a removed package's files, `uv cache clean` deletes them at once. An archive update no longer runs `pip install -r requirements.txt` in the venv: the setup it runs next installs the release's locks, and it releases the queue lock before that setup.

## Rejected alternatives

- **Keep swapping under running processes (ADR 0062).** It needs a second link setup cannot guarantee. This installation lost it to setup's own prune, and the GPU switch then failed on every attempt.
- **Drop `uv cache prune` and keep the swap.** It removes only this trigger: uv's copy fallback and a cleared cache still leave last links, and installations already pruned stay unable to switch until every Imprint process stops.
- **Build each changed runtime in a fresh venv and point `.venv` at it.** Nothing would be stopped on any OS. But clients' configurations name `.venv\Scripts\python.exe` directly, so `.venv` would become a junction; the first switch needs every process stopped anyway; and a switch needs disk for both runtimes. Offered to the user, who chose stopping the runtime.
- **Fail with a message naming the processes.** Honest, but the Settings switch would keep failing on every Windows machine without the links, since the dashboard asking for it always holds the runtime.
- **Keep bootstrap as a queue job.** Its output is a pipe to the dashboard, which setup restarts; the job's uv would lose its output mid-install.
- **Restart the server without making it wait.** Its launcher starts it at once, and it would load the packages being replaced.
- **Stop every runtime process on every OS.** Linux and macOS replace files under running processes, so stopping MCP servers there costs sessions for nothing. The server restarts on every OS, so it never serves from a half-replaced venv.
- **Signal the runtime change through the queue lock's holder.** Go locks the whole `queue.lock` and Windows locks are mandatory, so Python cannot read the holder while Go setup holds it; a separate file has one meaning.

## Consequences

- Switching the embedding runtime from Settings, an update or `imprint setup` works with Imprint running on Windows. MCP sessions there disconnect when, and only when, setup replaces or removes packages, and clients reconnect Imprint. On Linux and macOS they keep running and switch when they next start.
- The dashboard is down while setup downloads and installs a changed runtime (about a minute for the CUDA build) and comes back on it. Requests from an open page fail meanwhile.
- A job running when setup needs the runtime finishes first; `imprint setup`, bootstrap and the installer wait and say so.
- An `add-llm` that upgrades an installed package restarts the dashboard that runs it, so that first local chat fails once.
- The Queue page no longer shows bootstrap; Settings shows a runtime still missing with the end of `data/bootstrap.log`.
- Removed builds' files no longer wait in uv's cache. Installations whose cache an earlier prune emptied keep single-link files until a later change replaces them, which now frees the runtime first.
- A process started between the stop and the install (a hook, a reconnecting client) can still hold a file, and setup then fails as before; running it again succeeds.

## Invalidation conditions

- If uv moves in-use files aside on Windows, or Windows lets a loaded file's last link be replaced, stopping processes on Windows can go and ADR 0062's swap returns.
- If clients start Imprint through a launcher that can switch venvs, reopen building each changed runtime in a fresh venv, which stops nothing.
- If uv's dry run stops printing `Would uninstall` or `Would make no changes`, the gate misjudges installs; switch it to uv's structured output.
- If the desktop service imports from the venv before it waits, or a `.pth` file in the venv loads a library at startup, the restarted server holds files again; move its wait earlier.

## Validation

- Field evidence: the installation's logs, link counts, cache contents and process tree above, read through WSL on the machine (Windows 11, RTX 4080 Laptop GPU). uv 0.5.11 prune reproductions under `%TEMP%`: a hashed lock, the full base and CPU locks, the real bootstrap, and a cache beside the installation's. Dry-run output for no change, an addition and an upgrade, also with the pinned uv on Linux.
- End to end on native Windows, in a scratch tree under `%TEMP%` with an isolated `USERPROFILE` and this change's cross-compiled CLI and Python code:
  - The installation's state was reproduced: the CPU build with its cache entry removed (link count 1), an `imprint ui start` server on its own port, and a second process holding ONNX Runtime. The old setup's install command failed with the same `Access is denied`, leaving a half-swapped venv.
  - `imprint bootstrap` with `model.device=gpu` then stopped the holding process and its launcher, restarted the server, installed `onnxruntime-gpu` alone and passed the CUDA check in 12 s. The server came back under the same supervisor with a new pid, and `GET /api/config` reported nothing pending.
  - `PUT /api/config/model.device` `cpu` with another holding process started bootstrap detached. It stopped the holder, restarted the server that started it, installed the CPU build and removed the CUDA libraries, and the server came back on it.
- Go: `go test ./cmd/ ./internal/...` on Linux, and the `cmd` and `queuelock` test binaries cross-compiled and run natively on Windows. Covered: provisioning frees the runtime only for swaps, repairs and upgrades and skips installs that change nothing; `freeRuntime` restarts a registered server once and waits for it to exit; `stopOwnedProcesses` spares excepted processes; `withLauncher` adds a parent only from the runtime; Python waits while Go holds the runtime lock; releasing a handle twice leaves the next holder's lock alone. `go vet` for Linux, Windows and macOS. Natively on Windows, `TestListProcessesSeesThisProcess` fails when the binary runs without `-test.*` flags, which it expects on its command line, and passes with them.
- Python (Linux): provisioning API and runtime state, the detached bootstrap and its log, `_after_job`, and the desktop service waiting for `runtime.lock`; the full suite (626 tests, on this change merged with the dev branch) passes.
- Electron's suite (43 tests) and the dashboard's `tsc`, `eslint`, production build and full Playwright suite (116 tests, including the Settings specs for the started bootstrap and a missing runtime) pass.
- Not run: the desktop app (Electron) restarted by setup, the Windows installer and `imprint update`, macOS, and an archive update.
