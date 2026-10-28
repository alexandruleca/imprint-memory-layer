---
title: ADR 0096 — CPU Whisper runtime for audio imports
description: Pinned faster-whisper, CTranslate2 and PyAV wheels provide local multilingual transcription with explicit model downloads.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: transcription runtime/dependencies for audio ingestion
- Related: [ADR 0032](0032-one-onnx-runtime-per-environment.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md), [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md)

## Context

Audio ingestion needs Romanian and English transcription. Echo's LFM model is
English-only. Requiring another source-built executable would add compiler and
native bundle/release work before users could import ordinary audio. The current
runtime has one CPU ONNX build and a macOS 13 baseline. Upstream PyAV 16+ Apple
Silicon wheels require macOS 14.

The first native Small-model Romanian fixture recognized the language but
misheard several words and a number. Large v3 Turbo improved that sentence,
although the numeral remained wrong. Neither check is an accuracy benchmark.

## Decision

Install faster-whisper 1.2.1 and CTranslate2 4.8.4 in the existing hashed CPU
overlay, alongside its one ONNX Runtime distribution (used for VAD). Pin PyAV
19.0.2 on other targets and PyAV 15.1.0 on Apple Silicon macOS to retain the
available macOS 13 wheel. Preserve ONNX 1.31.0 on existing targets and 1.23.2 on
Intel macOS. No PyTorch, CUDA toolkit or NVIDIA runtime libraries are added.
Native inference explicitly selects CPU/int8, four threads and one worker.

Default to the multilingual Large v3 Turbo CTranslate2 conversion at
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, pinned revision
`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`. Offer the lighter multilingual Small
conversion at `Systran/faster-whisper-small`, revision
`536b0662742c02347bc0e980a01041f333bce120`. Pin the size and SHA256 of every
required model/tokenizer/configuration asset in `audio_ingest.py`.

Only an uncached audio import downloads model files. Opening settings, ordinary
text ingestion and runtime installation download no speech weights. Verified
weights live in a dedicated model directory under the data directory, shared
across workspaces without user audio. Verify completed files before loading;
rename partial downloads only after size/hash validation. Serialize downloads
with a process-safe lock. Loading uses local files only and never model-provided
code or an inference server. A changed model is released before loading another.

Use a bounded PyAV decoder directly and pass float32 windows to Whisper. Restrict
demuxers to audio container families, protocols to local file/pipe, and disable
external MP4 data references. PyAV's packaged codec libraries remove the system
FFmpeg requirement. Source text remains untrusted; recognition is not instruction
execution. Release distributions exclude model/user caches through the existing
data/cache exclusion policy.

## Rejected alternatives

- Reuse Echo's ASR: it does not supply the requested Romanian recognition.
- Add a whisper.cpp executable now: viable, but needs another native artifact
  build/distribution path when usable runtime wheels already exist.
- Make Small the only/default option after the native probe: its Romanian errors
  justify offering a larger multilingual option; Small remains a resource choice.
- Use the newest PyAV wheel on every target: its Apple Silicon wheel raises the
  effective macOS baseline to 14 or requires a local source build.
- Load a remote transcription provider or auto-select CUDA: neither is required
  for the local-file feature, and both add operational/dependency boundaries.

## Consequences

Audio-capable installations gain native wheel dependencies but no compiler,
system FFmpeg or extra model server. First use needs roughly 1.51 GiB for the
default model, or 464 MiB for Small. CPU processing of long recordings can be
slow. Model weights and Python runtime dependencies are separate downloads.
Mac ARM retains an older codec wheel for compatibility; that pin needs continued
upstream/security review and native execution checks. Wheel tags alone do not
establish supported execution. Transcription can still mishear names/numbers.

## Invalidation conditions

- If supported PyAV wheels restore macOS 13 Apple Silicon coverage, replace the
  compatibility pin after native validation.
- If codec updates can no longer safely support macOS 13, revisit the decoder or
  platform baseline explicitly instead of silently building from source.
- If held-out Romanian/English tests show a lighter model meets the required
  quality/resource tradeoff, reconsider the default using those measurements.
- If a portable GPU backend avoids new CUDA dependencies and materially improves
  long-recording latency, revisit CPU-only execution with native measurements.

## Validation

Locked requirements were regenerated with base constraints, retaining the
existing ONNX pins; hashed base/CPU installation resolution passed in dry-run.
Native package imports, CPU int8 support, MCP/API serving and Go setup/runtime
checks passed on Linux/WSL. PyPI wheel metadata was checked for CPython 3.12 on existing
Linux x64/arm64, Windows x64 and Mac Intel/ARM targets. Native Linux/WSL CPU
execution loaded checksum-verified models and decoded/transcribed English,
Romanian and mixed-language fixtures. Unit tests exercise partial/corrupt model
downloads and ingestion failure preservation. These are functional checks;
Windows/macOS native execution and broad transcription accuracy remain unverified.
