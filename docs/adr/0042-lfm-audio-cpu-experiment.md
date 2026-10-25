---
title: ADR 0042 — Small LFM audio experiment on CPU
description: Evaluate pinned native Q4 speech inference with read-only Imprint memory snapshots before live voice transport.
---

- Status: Accepted
- Date: 2026-10-24
- Scope: experimental offline turns only
- Related: [ADR 0041](0041-personaplex-startup-memory-prototype.md), [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md); [ADR 0043](0043-live-voice-and-echo-companion.md), which implements the separate live pilot
- Restructured: 2026-10-25

Follow-up: [ADR 0043](0043-live-voice-and-echo-companion.md) implements the separate
live pilot, its private HTTP worker and same-chat speech route. The offline CLI
decision and its historical validation below remain unchanged.

## Context

After authorizing PersonaPlex work, the user asked for smaller alternatives and explicitly selected LFM2.5-Audio-1.5B as the primary experiment. PersonaPlex's pinned model file is about 16.7 GB, while this machine exposes a 12 GB laptop GPU and a 16 GB WSL memory limit. Its setup was stopped after the user selected the new direction; no PersonaPlex speech performance was established. The earlier adapter remains available, and its rationale is preserved.

Liquid AI publishes a roughly 1.07 GB four-file Q4 audio bundle and a specialized native runner. A CPU pilot can test speech and memory conditioning while leaving GPU capacity for future avatar animation. Streaming output is not proof of native PersonaPlex-equivalent duplex behavior or tool calling. The model uses its own conversational backbone and custom LFM license.

## Decision

Add an explicitly invoked, Linux x64 CPU experiment with pinned asset sizes and SHA256 digests in the runtime manifest. Install into an explicitly selected external cache, validate archive contents, retain bundled notices and verify native files before execution. Do not install PyTorch or change Imprint's base/GPU runtime profile. Enable no new installer-supported platform.

Reuse the bounded read-only MCP snapshot builder with distinct LFM backend/revision identity validation. Pass snapshot evidence through a private temporary prompt file. Use the runner's exact supported system-prompt commands; do not put memory in process arguments or assume arbitrary system instructions are supported. Capture and discard raw native logs because the runner echoes prompts even when log disabling is requested.

Expose offline synthesis, recognition and speech/text question commands with bounded input/runtime limits, exclusive private response exports and owned-child cancellation. Parse both PCM16 and the runner's float32 WAV. Do not expose the native HTTP server or claim microphone streaming, barge-in, live MCP writes, persistence or avatar support in this step. Future live transport and memory/tool authority require their own implementation and validation.

## Rejected alternatives

- Keep PersonaPlex primary: the user selected the smaller model instead; no compatibility claim is made for completing its interrupted setup.
- Use the modular ASR → existing Qwen chat → TTS architecture: remains the preferred option if retaining the dashboard's conversational model is mandatory, but is not the selected experiment.
- Add an LFM PyTorch environment: increases dependencies and memory without being necessary for the first CPU proof.
- Expose the bundled HTTP server immediately: its authentication, origin policy, conversation ownership and cancellation semantics are not established for Imprint.
- Claim all official native runner platforms: downloaded artifacts and cross-compilation are not native validation.

## Consequences

The prototype produces real English speech and tests memory-conditioned answers on CPU with a small artifact set. A fresh native process/model load per turn adds overhead and is not a finished real-time assistant. The CLI protocol, exact task prompts and transcript markers are revision-specific. LFM snapshots are rejected by the PersonaPlex validator and vice versa.

Explicit caches and output directories are operator-owned experiments outside product distributions. Main setup/update/uninstall behavior is unchanged; automatic cleanup and supported runtime packaging remain future work. No secrets or real user memories are used as regression fixtures. Exported text/audio should be kept privately. Model license and runtime notices must remain accurate before any distribution work.

## Invalidation conditions

- If Liquid AI changes the bundle's digests or license, re-validate before moving the pin.
- If the native runner accepts arbitrary system instructions or stops echoing prompts in its logs, the prompt-file and log-discarding workarounds can be revisited.

## Validation

Real Linux/WSL2 CPU checks synthesize and recognize an English question, then generate spoken answers against two distinct read-only MCP snapshots backed by disposable retrieval fixtures. Different expected names must be returned. Record timings, sampled peak RSS, audio checks and limitations in [platform validation](../platform-validation.md) and the [experiment guide](../lfm-audio-prototype.md).

Focused regressions cover archive traversal/symlinks, idempotent installation/repair, native file tampering, WAV formats/invalid samples, input limits, backend identity isolation, prompt/privacy boundaries, workspace binding and owned-process timeout. Existing PersonaPlex tests remain applicable. No real microphone, echo, continuous listening/playback, interruption or native Windows/macOS/ARM result is claimed.
