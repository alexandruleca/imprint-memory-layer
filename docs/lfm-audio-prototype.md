---
title: LFM2.5-Audio local voice experiment
description: Install a verified small CPU speech runtime, prepare an Imprint memory snapshot and test English speech replies.
---

## Current implementation

Live follow-up: [Echo voice companion](live-voice.md) now adds streamed speech,
microphone controls, optional hands-free VAD and the floating particle head. This
page documents the earlier offline CLI, which remains available independently.

The user selected **LFM2.5-Audio-1.5B** as the primary voice experiment on 2026-10-24, after asking for a smaller alternative to PersonaPlex. Work continues on `feat/local-voice-avatar`. [ADR 0042](adr/0042-lfm-audio-cpu-experiment.md) records the runtime decision; the earlier PersonaPlex adapter (removed, [ADR 0118](adr/0118-personaplex-prototype-removed.md)) remains a separate experiment.

This implementation performs **offline English turns** with Liquid AI's official Q4 artifacts and specialized native audio CLI. It supports speech synthesis, recognition, typed questions with spoken answers, and WAV questions with spoken answers. Replies can use a bounded read-only Imprint MCP memory snapshot. The snapshot builder is shared with the earlier prototype, while backend identity validation keeps their session files distinct.

The offline CLI does not supply a live microphone UI, streaming playback, barge-in, echo cancellation, live MCP tool calls, conversation history or the floating head. Its selected model supplies its own conversational backbone; the separate live adapter offers the existing local chat reasoner as well.

## Install explicitly

Use Imprint's provisioned Python from the repository root:

```bash
/path/to/imprint-python -m imprint.lfm_audio install \
  --runtime-dir /path/to/private/lfm-audio
```

The enabled native target is **Linux x64, including WSL2**, using CPU inference. Windows, macOS, Linux ARM and GPU execution are not enabled or validated by this prototype. Official assets for other platforms do not by themselves establish Imprint support.

The four required weight files total **1,074,794,688 bytes**, about 1.07 GB, plus a 13.2 MB native runner archive. All downloads use revision `7d525f883a077e20afb782f2ff618edcae0e39e4` of [Liquid AI's official GGUF repository](https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-GGUF). Sizes and SHA256 digests are checked against the source-controlled runtime manifest; model files and unpacked native files are verified again before execution. Install is serialized, reuses verified files, repairs managed damaged artifacts and validates archive paths before extraction. License notices from the runner archive are preserved. The model declares the custom LFM Open License v1.0; the native runner has its own bundled notices.

No PyTorch environment is needed, no model is downloaded implicitly during a reply, and Imprint's base/GPU profile is unchanged. Keep this explicitly selected cache outside the repository and distributions. The prototype does not register the cache with the main installer/uninstaller; remove that dedicated directory deliberately when no longer needed, preserving any response exports you want to keep.

## Prepare an Imprint memory snapshot

```bash
/path/to/imprint-python -m imprint.lfm_audio prepare \
  --data-dir /path/to/imprint/data \
  --workspace default \
  --project my-project \
  --query "architecture decisions relevant to the voice assistant" \
  --output /path/to/private/lfm-session.json
```

The explicit workspace and optional project filter are forwarded through local stdio MCP. Only `device_context` and compact `search` are called. Searches may initialize ordinary embedding/Qdrant caches; no dashboard LLM or memory write is invoked. Up to four queries are accepted, with complete evidence responses preserved within the configured character budget. `--max-chars` defaults to 8000, is bounded to 2048–16000, and is not a token guarantee. Omitted zero-based query indexes are reported. Oversized model context fails rather than silently claiming all evidence was used.

The session is a plaintext private export containing evidence and provenance; existing files are not overwritten. Replies retain the selected workspace even if another window changes the global workspace. It is a snapshot, so later retrieval or writes require a new explicit preparation step. Prompt escaping and instructions do not guarantee protection against model-level prompt injection.

## Try speech

Ask a typed question with a spoken response:

```bash
/path/to/imprint-python -m imprint.lfm_audio reply \
  --runtime-dir /path/to/private/lfm-audio \
  --session /path/to/private/lfm-session.json \
  --text "What did we decide about the assistant?" \
  --output-dir /path/to/private/answer-001
```

Use a recorded WAV instead by replacing `--text` with `--audio /path/to/question.wav`. Input is limited to 30 seconds, mono PCM16 or float32 WAV at 8–48 kHz. The runner handles its audio preprocessing; this is complete-utterance input, not concurrent listening and speaking.

Standalone synthesis and recognition are also available:

```bash
/path/to/imprint-python -m imprint.lfm_audio tts \
  --runtime-dir /path/to/private/lfm-audio \
  --text "Hello, I am Imprint." \
  --voice us_female \
  --output-dir /path/to/private/greeting-001

/path/to/imprint-python -m imprint.lfm_audio asr \
  --runtime-dir /path/to/private/lfm-audio \
  --audio /path/to/question.wav \
  --output-dir /path/to/private/transcription-001
```

The four voice choices apply to standalone TTS. Interleaved replies use the native model's default voice; voice selection for those replies has not been implemented. Each output directory must be new. Speech turns produce `response.wav` and a `result.json` with generated text, elapsed time, sampled peak RSS and audio metadata; ASR produces the result JSON alone. The official runner currently emits float32 WAV, supported by the validator. Keep these plaintext/audio exports private.

Each turn launches the CLI anew with four CPU threads and a 4096-token context. `--threads`, `--timeout` and `--max-tokens` adjust bounded limits. The launcher passes memory through a temporary private prompt file, removes that file and raw native logs after completion, and prints the answer and metrics only. This is necessary because the pinned runner echoes prompts even with its `--log-disable` flag. The input-file escape setting is disabled, so escaped evidence delimiters are not reinterpreted by the argument reader. Native child environments omit Hugging Face credentials and unrelated model-provider settings. A timeout or Ctrl+C stops only the owned child process.

The smoke test checks that output contains non-silent 24 kHz audio. Successful synthesis does not establish intelligibility, pronunciation accuracy or answer correctness.

## Reproduce the real-model smoke

```bash
PYTHONPATH=. /path/to/imprint-python scripts/smoke-lfm-audio.py \
  --runtime-dir /path/to/private/lfm-audio \
  --output-dir /path/to/private/lfm-smoke-001
```

This uses the real native runner and model for TTS, ASR and two speech-to-speech replies. MCP discovery/calls use Imprint's actual tool registry with a disposable retrieval fixture at the storage boundary. The same synthesized question is answered using two snapshots whose mascot names differ. The generated transcripts must select the current name and omit an unrelated distractor. It uses no real user memories, microphone or client profile.

The observed CPU result is promising for a smaller assistant, while remaining a functional smoke rather than a grounding or voice-quality benchmark. See [platform validation](platform-validation.md#lfm25-audio-cpu-experiment-2026-10-24) for recorded measurements and limits. Continuous voice transport, first-audio latency, cancellation during playback, speaker echo, live memory tools and native desktop behavior remain the next gates.

Executed on 2026-10-24: two real CPU smoke runs passed all four model turns (TTS, ASR, Nimbus reply and Comet reply), and the full 295-test Python suite passed. Complete reply generation with a new native process took 2.95–4.52 seconds using four threads, excluding asset verification; sampled peak RSS was approximately 1754 MiB. First-audio latency and filesystem-cold performance were not measured. The [raw evidence](evidence/2026-10-24-lfm-audio-cpu-smoke.json) preserves both runs rather than selecting the faster observation.
