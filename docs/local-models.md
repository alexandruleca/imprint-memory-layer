---
title: Local helper models
description: Configure the shared optional Qwen3.5 helper for tags, session summaries and dashboard tools.
---

The default optional helper is [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), Apache-2.0, using [Unsloth's Q4_K_M GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF). Text-only inference needs no vision projector. Its 2.74 GB file is pinned to revision `e87f176479d0855a907a41277aca2f8ee7a09523` with a source-controlled SHA256. First use needs internet; subsequent inference uses local files. Custom GGUF repositories retain their configured downloads and should be pinned/verified by their operator.

Embeddings, ordinary search and explicit MCP stores do not require this helper. LLM tagging and session summaries remain opt-in. The dashboard loads the model only when chat is invoked. The first local chat installs the optional `llama-cpp-python` binding and downloads the model if either is missing, and shows the progress in the chat; `imprint profile add-llm` installs the binding from the CLI (ADR 0035). Windows x64 and Linux x64/arm64 install upstream's prebuilt CPU wheel; macOS and other platforms build it from source, which needs a C/C++ toolchain (Xcode Command Line Tools on macOS).

## Enable the roles you want

```bash
imprint config set chat.provider local
imprint config set tagger.llm_provider local
imprint config set tagger.llm true
imprint config set summarizer.provider local
imprint config set summarizer.enabled true
```

To replace previously persisted helper choices, set both `chat.model_repo` and `tagger.local.model_repo` to `unsloth/Qwen3.5-4B-GGUF`, both `chat.model_file` and `tagger.local.model_file` to `Qwen3.5-4B-Q4_K_M.gguf`, and both context settings (`chat.n_ctx`, `tagger.local.n_ctx`) to `8192`. Reset any explicit `chat.model_path` / `tagger.local.model_path` override if you want managed downloads. Existing user choices are preserved until explicitly changed. Restart processes after editing settings.

All three roles use one cached GGUF file by default. Chat and tagging reuse the same model object **within a process** when path, context and GPU-layer settings match. Summaries use `tagger.local.*` settings. A shared inference lock serializes local streaming and classification. Different configurations or separate dashboard/CLI/MCP processes can still load separate instances. For sharing across processes, select an existing Ollama/vLLM endpoint explicitly; no new server is installed or started automatically.

## Bounded generation

The default context is 8192 tokens. Embeddings default to CPU to leave GPU headroom; helper GPU layers default to `-1` (all supported layers). Set both `chat.n_gpu_layers` and `tagger.local.n_gpu_layers` to `0` for CPU helper inference. Context, runtime buffers and other applications add memory beyond quantized file size. Larger contexts and a 9B helper remain measured opt-in choices.

Local helper inference uses four CPU threads and a 256-token prompt batch. An opt-in native smoke is available as `PYTHONPATH=. <venv-python> scripts/smoke-local-helper.py --model-path /path/to/Qwen3.5-4B-Q4_K_M.gguf`. It verifies the pinned checksum, sharing, non-thinking template, structured classification/summary and a dashboard tool round trip with mocked storage, in a temporary data directory. Without `--model-path`, it downloads the model into that temporary directory. This is a small functional check, not a quality benchmark.

The embedded GGUF chat template is rendered with `enable_thinking=false`. This replaces the ineffective `/no_think` text prefix for local Qwen3.5. Missing or incompatible templates fail at load. Classification and summaries use JSON-schema grammars, followed by existing application validation. Valid JSON does not establish factual correctness.

Chat checks actual formatted prompt tokens and drops oldest conversation history to fit; an oversized current input fails clearly. Summaries keep a 5000-token estimated transcript budget, then cap actual transcript tokens to the local context while preserving instructions and recent transcript content. Ordinary classification has a bounded input and rejects context overflow. Existing tool iteration limits and the tool allowlist remain in effect. Dashboard search forwards source/mode/offset filters and uses the workspace-specific wake state.

Remote provider configuration remains supported. Non-thinking template control here applies to the in-process runtime; configure thinking controls separately at remote servers. An external model is not automatically equivalent to the pinned GGUF.

See [model selection rationale](local-model-selection.md), [tagging](tagging.md) and [ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md) for validation and remaining evaluation work.
