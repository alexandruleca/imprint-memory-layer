---
title: Local helper models
description: Configure the shared optional Qwen3.5 helper for tags, session summaries, recall planning and dashboard tools.
---

The default optional helper is [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), Apache-2.0, using [Unsloth's Q4_K_M GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF). Text-only inference needs no vision projector. Its 2.74 GB file is pinned to revision `e87f176479d0855a907a41277aca2f8ee7a09523` with a source-controlled SHA256. First use needs internet; subsequent inference uses local files. Custom GGUF repositories retain their configured downloads and should be pinned/verified by their operator.

Embeddings, ordinary search and explicit MCP stores do not require this helper. LLM tagging and session summaries remain opt-in, and their prompts are redacted before any provider sees them ([secret redaction](redaction.md#text-sent-to-models)). The dashboard loads the model only when chat is invoked. The helper runs on llama.cpp's binding, `llama-cpp-python`, which every installation includes because embeddings run on it too ([ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). The first local chat only downloads the model if it is missing, and shows the progress in the chat (ADR 0035). A binding that does not load is a damaged installation; chat says to run `imprint bootstrap --non-interactive`. Windows x64 and Linux x64/arm64 install upstream's prebuilt wheel; macOS and other platforms build it from source, which needs a C/C++ toolchain (Xcode Command Line Tools on macOS), until Imprint pins prebuilt Metal wheels from its own release workflow (planned).

## Enable the roles you want

```bash
imprint config set chat.provider local
imprint config set tagger.llm_provider local
imprint config set tagger.llm true
imprint config set summarizer.provider local
imprint config set summarizer.enabled true
imprint config set recall.llm true          # uses tagger.llm_provider when it is local
```

To replace previously persisted helper choices, set both `chat.model_repo` and `tagger.local.model_repo` to `unsloth/Qwen3.5-4B-GGUF`, both `chat.model_file` and `tagger.local.model_file` to `Qwen3.5-4B-Q4_K_M.gguf`, and both context settings (`chat.n_ctx`, `tagger.local.n_ctx`) to `8192`. Reset any explicit `chat.model_path` / `tagger.local.model_path` override if you want managed downloads. Existing user choices are preserved until explicitly changed. Restart processes after editing settings.

All four roles use one cached GGUF file by default. Chat and tagging reuse the same model object **within a process** when path, context and GPU-layer settings match. Summaries and recall use `tagger.local.*` settings; recall loads the model in the MCP server process, never downloads it, and uses only a model on this computer ([configuration](configuration.md#recall)). A shared inference lock serializes local streaming and classification. Different configurations or separate dashboard/CLI/MCP processes can still load separate instances. For sharing across processes, select an existing Ollama/vLLM endpoint explicitly; no new server is installed or started automatically.

## Bounded generation

The default context is 8192 tokens. Helper GPU layers default to `-1` (all supported layers). On the `gpu` and `auto` profiles setup installs llama.cpp's Vulkan build on Windows and Linux x64 (Metal on macOS) when it finds a GPU, so the helper offloads without a CUDA toolkit ([ADR 0049](adr/0049-llama-gpu-offload-through-vulkan-wheels.md)). Each model runs whole on one GPU, the one `model.gpu_device` selects (`auto`: the discrete GPU with the most memory), rather than split across several; a GPU that cannot load it leaves it on the CPU with a note on standard error ([ADR 0087](adr/0087-llama-cpp-runs-each-model-on-one-gpu.md), [ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)). Embeddings use the same GPU when `model.device` allows, so the helper and the embedding model share its memory. Set both `chat.n_gpu_layers` and `tagger.local.n_gpu_layers` to `0` for CPU helper inference. Context, runtime buffers and other applications add memory beyond quantized file size. Larger contexts and a 9B helper remain measured opt-in choices.

Local helper inference uses four CPU threads and a 256-token prompt batch. An opt-in native smoke is available as `PYTHONPATH=. <venv-python> scripts/smoke-local-helper.py --model-path /path/to/Qwen3.5-4B-Q4_K_M.gguf`. It verifies the pinned checksum, sharing, non-thinking template, structured classification/summary and a dashboard tool round trip with mocked storage, in a temporary data directory. Without `--model-path`, it downloads the model into that temporary directory. This is a small functional check, not a quality benchmark.

The embedded GGUF chat template is rendered with `enable_thinking=false`. This replaces the ineffective `/no_think` text prefix for local Qwen3.5. Missing or incompatible templates fail at load. Summaries and recall plans use a JSON-schema grammar; for recall plans it was no slower than unconstrained output on CPU, which spends more tokens on pretty-printed JSON ([ADR 0058](adr/0058-recall-batched-reads-with-optional-local-planning.md)). Classification sends its prompt unconstrained, because on a GPU the grammar cost more than the inference, and its reply is validated like a remote provider's ([ADR 0049](adr/0049-llama-gpu-offload-through-vulkan-wheels.md)). Both are followed by existing application validation. Valid JSON does not establish factual correctness.

Chat calls tools through each model's native protocol ([ADR 0059](adr/0059-chat-speaks-each-models-native-tool-protocol.md)). One registry holds every tool's JSON schema. Locally, the schemas, earlier calls and tool results are rendered by the GGUF's own chat template. The reply is parsed in the call format of the model's family, chosen by `chat.tool_format` (Settings → Chat → Tool-call format; [ADR 0061](adr/0061-local-tool-call-formats.md)):

| Value | Models | A call in the reply |
|---|---|---|
| `auto` (default) | Detected from the loaded GGUF's template | |
| `qwen-xml` | Qwen3.5, Qwen3-Coder | `<tool_call><function=NAME><parameter=P>value</parameter></function></tool_call>` |
| `hermes` | Qwen2.5, Qwen3, Hermes | `<tool_call>{"name": …, "arguments": {…}}</tool_call>` |
| `llama3` | Llama 3.1–3.3 | the whole reply is `{"name": …, "parameters": {…}}`, optionally after `<|python_tag|>` |
| `mistral` | Mistral, Mistral Nemo | `[TOOL_CALLS][{"name": …, "arguments": {…}}]` |
| `none` | Any | the chat runs without tools |

`auto` asks the template itself: it renders a probe tool call the way its model was trained to write calls, and the format whose parser reads that back is chosen. A template that renders no call any format reads (no tools support, or an unknown grammar) chats without tools, and the conversation's first reply shows a notice naming the setting; choose a format there if the model can call tools. Each parser accepts only its family's documented shape. Arguments are then typed by each tool's schema, for every format and for remote providers: a string given for a number or boolean parameter is read as JSON, and string values stay verbatim, multi-line included. A value of the wrong type is returned to the model as a tool error. Generation decodes special tokens as text, because markers such as Mistral's `[TOOL_CALLS]` are special tokens. A template that takes tools other than through the standard `tools` variable, such as Phi-4-mini's, is not supported, and neither is a model whose tool template is stored separately (`tokenizer.chat_template.tool_use`, as in Hermes 3). Remote providers use their API's function calling: `tools`, streamed `tool_calls` and `tool` messages. A vLLM server must run with `--enable-auto-tool-choice` and a `--tool-call-parser` for its model. The Anthropic provider uses its OpenAI-compatible endpoint `https://api.anthropic.com/v1/`.

Chat checks actual formatted prompt tokens, tool schemas included (about 1,700 tokens with Qwen3.5-4B's template), and drops oldest conversation history to fit; an oversized current input fails clearly. Summaries keep a 5000-token estimated transcript budget, then cap actual transcript tokens to the local context while preserving instructions and recent transcript content. Ordinary classification has a bounded input and rejects context overflow. `chat.max_tool_iters` bounds model turns that call tools; voice mode offers only the read tools. Dashboard search forwards source/mode/offset filters and uses the workspace-specific wake state.

Remote provider configuration remains supported. Non-thinking template control here applies to the in-process runtime; configure thinking controls separately at remote servers. An external model is not automatically equivalent to the pinned GGUF.

See [model selection rationale](local-model-selection.md), [tagging](tagging.md) and [ADR 0010](adr/0010-qwen-defaults-and-explicit-reindex.md) for validation and remaining evaluation work.
