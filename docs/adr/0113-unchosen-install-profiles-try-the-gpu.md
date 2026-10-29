---
title: ADR 0113 — Unchosen install profiles try the GPU
description: Only a profile the user picked is kept; installer defaults resolve to auto, so setup and update try llama.cpp's GPU build.
---

- Status: Accepted
- Date: 2026-10-29
- Amends:
  - [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) — a stored profile that nobody chose no longer pins the CPU build
- Related: [ADR 0012](0012-native-package-runtime-and-silent-installs.md) (Windows silent installs keep their CPU default), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md)

## Context

A Linux machine with an RTX 5070 Ti and a working Vulkan driver embedded on the CPU, and `imprint doctor` reported "llama.cpp found no GPU it can use". The real cause was elsewhere:

- `install.sh` piped from curl has no terminal, so it chose `cpu` without asking and passed `--profile cpu`.
- Pressing Enter at its prompt also chose `cpu`, although the prompt shows `gpu` as the default.
- `data/profile.json` recorded `cpu`, and every later `imprint setup` and `imprint update` installed the CPU build again.

On the same machine, the Vulkan wheel from `llm-gpu.lock.txt`, installed in a scratch venv, found the GPU (`ggml_vulkan: 0 = NVIDIA GeForce RTX 5070 Ti`, offload `True`).

## Decision

- `profile.json` gains `chosen`, set when the profile came from `--profile`, `IMPRINT_PROFILE` or `imprint profile set`.
- Setup keeps a stored profile only when it is marked `chosen`. Any other stored value, including every `profile.json` written before this field existed, resolves to `auto`: setup installs the GPU build and keeps it when llama.cpp finds a GPU. When that replaces a stored `cpu`, setup says so and names `imprint profile set cpu`.
- `install.sh` passes a profile only when the user typed one or answered its prompt. It prompts only on a fresh interactive install, and Enter picks `gpu`, as the prompt says.
- `imprint doctor` and Settings say that the cpu install profile installed the CPU build, rather than that no GPU was found.

## Rejected alternatives

- **Change only `install.sh`.** Every existing install would keep its recorded `cpu` until the user found `imprint profile set`; the owner asked for setup and bootstrap to fix it themselves.
- **Treat a stored `cpu` as a choice unless it matches a known installer default.** `profile.json` never recorded where its value came from, so a default cannot be told apart from a choice.

## Consequences

- An existing installation that recorded `cpu`, chosen or not, switches to `auto` on its next `imprint setup` or `imprint update`. Where llama.cpp finds no GPU, `auto` installs the CPU build as before. A user who had deliberately picked the CPU on a GPU machine gets the GPU build once and has to run `imprint profile set cpu`.
- Windows silent installs still pass `-Profile cpu` on a first install (ADR 0012). That is an explicit value, so it stays CPU.

## Invalidation conditions

- If users report losing a deliberate CPU choice after an update, then the migration needs to ask before switching.
- If the GPU build becomes the default for every profile, or the profiles are removed, then `chosen` is obsolete.

## Validation

- `go test ./cmd/`, including `TestResolveInstallProfile`: fresh install, stored default, stored choice, flag, environment, unknown value.
- `tests/test_llama_runtime.py`: `cpu_profile`.
- `imprint doctor` on the machine above reports `CPU: the cpu install profile installed llama.cpp's CPU build`.
- `bash -n install.sh`, including the empty-arguments expansion under `set -u`.
- Not executed: a full `imprint setup` that switches an installed CPU build to the GPU build.
