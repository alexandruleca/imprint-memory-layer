---
title: Configuration
description: Tune Imprint — model, chunker, tagger, Qdrant, and queue settings via `imprint config set`, with environment-variable overrides and precedence rules.
---
All settings can be persisted via `imprint config` instead of setting environment variables. Settings are stored in `data/config.json` (gitignored).

**Precedence:** env var > config.json > hardcoded default. Environment variables always win, so you can override config.json for one-off runs.

```bash
# Select the supported Qwen default (then rebuild; see embeddings.md)
imprint config set model.name onnx-community/Qwen3-Embedding-0.6B-ONNX
imprint config set model.dim 1024
imprint config set model.seq_length 2048

# Use local Ollama for LLM tagging
imprint config set tagger.llm true
imprint config set tagger.llm_provider ollama
imprint config set tagger.llm_model llama3.2

# Or fully in-process via llama-cpp (no server to run)
imprint config set tagger.llm true
imprint config set tagger.llm_provider local

# Custom Qdrant server
imprint config set qdrant.host 192.168.1.50
imprint config set qdrant.no_spawn true

# See what's changed
imprint config

# Show one setting + where the current value came from
imprint config get model.device

# One-off env override (doesn't persist)
IMPRINT_DEVICE=gpu imprint ingest ~/code

# Remove one override
imprint config reset model.device

# Wipe all overrides
imprint config reset --all
```

`imprint config` groups settings visually by prefix (`model.*`, `huggingface.*`, `qdrant.*`, `chunker.*`, `tagger.*`, `tagger.local.*`, `ingest.*`, `chat.*`, `summarizer.*`). Non-default values are highlighted by source (`config.json` cyan, `env` yellow).

The dashboard's **Settings** page edits the same values. A setting with known values, such as `model.device`, a provider or `model.gpu_device` (the GPUs this device reports), is a dropdown. **Custom value…** accepts anything else the setting allows. A value set by an environment variable is shown read-only. The reset button (remove, for a secret) deletes the `config.json` override, like `imprint config reset <key>`. Both the CLI and the dashboard refuse a value outside a fixed set.

## Hugging Face token

Imprint downloads the embedding model and the optional local helper GGUF from the Hugging Face Hub. Without a token those downloads are anonymous: they work for the default public models but are rate-limited, and huggingface_hub warns about unauthenticated requests. Gated or private repositories need a token. Create a read token at <https://huggingface.co/settings/tokens>, then store it in one of these ways:

- Dashboard: **Settings → Hugging Face → Token**. Click **Not set** (or the masked value), paste the token and save. The field is a password input and starts empty.
- CLI: `imprint config set huggingface.token hf_...`. The token appears on the command line and in shell history, so prefer the dashboard or the environment variable on shared machines.
- Environment: export the standard `HF_TOKEN` variable. It takes precedence over the stored token.

Every Imprint process reads the token when it downloads, so a token saved in the dashboard applies to the running dashboard server and to the ingest, refresh and learn processes it starts. It is also used by MCP and hook processes, with no restart needed. Embedding downloads pass it to `huggingface_hub`, and GGUF downloads send it as an `Authorization` header to `huggingface.co`. httpx drops that header if a redirect leaves the host. With neither `HF_TOKEN` nor a stored token set, downloads use huggingface_hub's own login (`hf auth login`). One case is not covered: Chonkie's own model2vec fallback, which runs only when Imprint's embedding model cannot load, sees `HF_TOKEN` and `hf auth login` but not the stored token.

`huggingface.token` is a **secret** setting. `imprint config`, `imprint config get`/`set`, `imprint status` and `GET /api/config` show only `****` plus its last four characters, and only for tokens of 12 characters or more. The raw value is stored only in `data/config.json`. Imprint writes that file through a temporary file created with owner-only permissions (`0600` on macOS/Linux). On Windows it inherits the per-user install directory's ACL. Uninstalling without keeping data deletes it. See [ADR 0033](adr/0033-stored-secret-settings.md).

## All Settings

| Key | Default | Description |
|-----|---------|-------------|
| **Embedding model** | | |
| `model.name` | `onnx-community/Qwen3-Embedding-0.6B-ONNX` | HuggingFace embedding model repo |
| `model.revision` | `auto` | Pinned default export revision; custom repos use main unless pinned |
| `model.file` | `auto` | ONNX model file (auto = pick by device) |
| `model.device` | `cpu` | Compute device: auto / cpu / gpu |
| `model.dim` | `1024` | Embedding vector dimension |
| `model.seq_length` | `2048` | Token cap per embed call |
| `model.threads` | `4` | CPU intra-op threads for ONNX |
| `model.gpu_mem_mb` | `2048` | VRAM cap for ORT CUDA arena (MB) |
| `model.gpu_device` | `auto` | CUDA device index; `auto` picks the first GPU the installed CUDA runtime supports ([ADR 0045](adr/0045-setting-choices-and-gpu-auto-select.md)) |
| `model.batch_size` | `0` | Embedding batch size (0 = auto: 4 explicit GPU, 1 CPU/auto) |
| `model.pooling` | `auto` | Pooling strategy: auto / cls / mean / last |
| `model.query_instruction` | `auto` | Model-specific query task; Qwen wraps Instruct/Query |
| `model.output` | `auto` | Named ONNX output selection |
| **Hugging Face Hub** | | |
| `huggingface.token` | — | Access token for model downloads. Secret: displayed masked. Its env var is the standard `HF_TOKEN`, not an `IMPRINT_*` name |
| **Qdrant** | | |
| `qdrant.host` | `127.0.0.1` | Qdrant host; auto-spawn requires a literal loopback IP or `localhost` |
| `qdrant.port` | `6333` | Qdrant HTTP port |
| `qdrant.grpc_port` | `6334` | Qdrant gRPC port |
| `qdrant.version` | `v1.17.1` | Pinned Qdrant release for auto-download |
| `qdrant.no_spawn` | `false` | Skip auto-spawn (BYO server) |
| **Chunker** | | |
| `chunker.overlap` | `400` | Sliding overlap chars between chunks |
| `chunker.size_code` | `4000` | Soft target chunk size for code |
| `chunker.size_prose` | `6000` | Soft target chunk size for prose |
| `chunker.hard_max` | `8000` | Absolute max chunk size |
| `chunker.semantic_threshold` | `0.5` | Topic-shift threshold for SemanticChunker (lower = sharper splits) |
| **Tagger** | | |
| `tagger.zero_shot` | `true` | Enable zero-shot topic tagging |
| `tagger.llm` | `false` | Enable LLM topic tagging (replaces zero-shot during ingest/refresh) |
| `tagger.llm_provider` | `local` | LLM provider: anthropic / openai / ollama / vllm / gemini / local |
| `tagger.llm_model` | — | Server model override (default per provider); local uses GGUF settings |
| `tagger.llm_base_url` | — | LLM tagger API base URL override |
| **Tagger — local model (llama-cpp, provider=`local`)** | | |
| `tagger.local.model_repo` | `unsloth/Qwen3.5-4B-GGUF` | HF repo for GGUF auto-download |
| `tagger.local.model_file` | `Qwen3.5-4B-Q4_K_M.gguf` | GGUF filename within the repo |
| `tagger.local.model_path` | — | Absolute path to a local GGUF (overrides repo/file) |
| `tagger.local.n_ctx` | `8192` | Tagger context window in tokens |
| `tagger.local.n_gpu_layers` | `-1` | GPU layers to offload (-1 = all) |
| **Ingest (docs + URLs)** | | |
| `ingest.doc_formats` | `pdf,docx,pptx,xlsx,csv,epub,rtf,html,eml,json` | Comma-separated doc formats enabled for the file walker |
| `ingest.ocr_enabled` | `false` | OCR for scanned PDFs + images (requires `tesseract`; PDFs also need poppler). Without it, a PDF with no text layer is skipped with this hint, not failed, and retried once OCR settings or tools change ([ADR 0040](adr/0040-documents-with-nothing-to-index.md)) |
| `ingest.ocr_lang` | `eng` | Tesseract language codes (e.g. `eng+fra`) |
| `ingest.max_doc_size_mb` | `25` | Per-file byte cap for document extraction |
| `ingest.url_timeout_sec` | `30` | HTTP connect timeout for URL fetch |
| `ingest.url_read_timeout_sec` | `300` | Per-chunk read timeout for URL fetch (raise for very large files) |
| `ingest.url_user_agent` | `imprint/1.0` | HTTP User-Agent header |
| `ingest.url_respect_robots` | `true` | Check `robots.txt` before fetching |
| `ingest.use_kreuzberg` | `false` | Use kreuzberg (ELv2) for doc extraction — replaces PDF/DOCX/image extractors, adds archives (.zip, .7z, .tar, Apple iWork, ODF, .eml, images via OCR). Install with `pip install kreuzberg[all]`. **ELv2 — not permitted for SaaS/hosted use.** |
| `ingest.use_obscura` | `false` | Use obscura headless browser for HTML URL fetch (JS rendering). Requires `obscura` binary in PATH. Enables full-fidelity ingestion of JS-heavy SPAs and docs sites. |
| `ingest.url_follow` | `false` | Follow same-domain links found on ingested pages (BFS crawl) |
| `ingest.url_follow_depth` | `1` | Max link-follow depth (`1` = links on seed pages only) |
| `ingest.url_follow_max` | `20` | Max discovered URLs to queue per ingest run |
| **Chat (dashboard panel)** | | |
| `voice.avatar` | `echo` | Echo's look: `echo` (a holographic core whose rings turn and pulse as it speaks), `iris` (indigo straight cut, pale lavender eyes) or `kai` (spiky orange hair, a determined frown). Also chosen on the Voice page. See [Echo voice](live-voice.md). |
| `voice.runtime_dir` | — | Explicit external cache containing the built native live speech worker for Windows x64, macOS Intel/Apple Silicon or Linux x64; `IMPRINT_VOICE_RUNTIME_DIR` overrides it. See [Echo voice](live-voice.md). |
| `chat.enabled` | `true` | Enable the dashboard chat panel |
| `chat.provider` | `local` | Chat provider: local / vllm / openai / ollama / gemini / anthropic |
| `chat.model` | — | Model name for remote providers (default per provider) |
| `chat.base_url` | — | Base URL override for OpenAI-compat providers |
| `chat.model_repo` | `unsloth/Qwen3.5-4B-GGUF` | HF repo for GGUF auto-download (local provider) |
| `chat.model_file` | `Qwen3.5-4B-Q4_K_M.gguf` | GGUF filename within the repo |
| `chat.model_path` | — | Absolute path to a local GGUF (overrides repo/file) |
| `chat.n_ctx` | `8192` | Chat context window tokens |
| `chat.n_gpu_layers` | `-1` | GPU layers to offload (-1 = all) |
| `chat.max_tokens` | `1024` | Max tokens per chat response |
| `chat.temperature` | `0.3` | Chat sampling temperature |
| `chat.max_tool_iters` | `6` | Max tool-call iterations per chat turn |
| **Session summarizer (opt-in, Stop hook)** | | |
| `summarizer.enabled` | `false` | Enable the LLM-based session summarizer |
| `summarizer.provider` | `local` | Provider: local / ollama / vllm / anthropic / openai / gemini |
| `summarizer.model` | `qwen3.5:4b` | Model name for the chosen provider |
| `summarizer.base_url` | — | API base URL override |
| `summarizer.min_messages` | `5` | Skip sessions with fewer messages |
| `summarizer.max_input_tokens` | `5000` | Truncate transcript before summarizing |
| **Other** | | |
| `collection` | `memories` | Default Qdrant collection name (workspace suffix is appended automatically) |

Each setting also has an env var, `IMPRINT_*` except `HF_TOKEN` (shown via `imprint config get <key>`). Env vars still work and take priority over config.json.

## Local service boundaries

Managed Qdrant binds both HTTP and gRPC to loopback, disables cluster mode and browser CORS, and refuses nonlocal `qdrant.host` values before connecting or spawning. `localhost` is bound as `127.0.0.1`. Using an external Qdrant requires explicitly setting `qdrant.no_spawn=true`; its network isolation, authentication and TLS are then the operator's responsibility. Imprint cannot establish the bind policy of an already-running or independently managed server.

The dashboard and its API share a loopback listener on port 8420. Requests must have a loopback connection address and local Host/Origin headers. The supported launcher ignores forwarded client headers. Container mode is an explicit exception to the connection-address check because Docker NAT changes the peer address: publish only `127.0.0.1:8420:8420` and use a trusted container network. Host/Origin checks do not authenticate a remote client that forges those headers. See [Docker](docker.md) and [ADR 0024](adr/0024-enforce-local-service-boundaries.md).

Ollama is independently managed. Imprint's Ollama endpoints default to `http://localhost:11434/v1`; configure the Ollama process itself with `OLLAMA_HOST=127.0.0.1:11434`, then restart it. A local client URL does not constrain the server's listening interfaces. See [Ollama's server configuration](https://docs.ollama.com/faq#how-do-i-configure-ollama-server).

## Env-only settings

These don't live in `config.json` because they're path-shaped infra knobs the config system is not designed to manage. Set them at process start:

| Env var | Default | Purpose |
|---|---|---|
| `IMPRINT_DATA_DIR` | `<repo>/data` or `~/.local/share/imprint/data` | Root for workspaces, Qdrant storage, SQLite graphs, config.json. Override for multi-install setups or when running out of a read-only tree. |
| `IMPRINT_QDRANT_BIN` | auto (downloaded into `data/qdrant-bin/`) | Path to a system-installed `qdrant` binary, bypassing the pinned auto-download. |
| `IMPRINT_MCP_IDLE_S` | Retired | Ignored compatibility setting. HTTP Qdrant clients retain their connection pool until explicit cleanup or process exit; there is no embedded lock to release. See ADR 0022. |

## Source limits and operation workspace

`ingest.max_file_bytes` (`IMPRINT_INGEST_MAX_FILE_BYTES`) defaults to 2,000,000 for source-text indexing. Oversize/undecodable sources retain their old chunks and report an incomplete run; there is no silent 50,000-character cutoff. Document extraction keeps `ingest.max_doc_size_mb` separately. LLM tagging remains opt-in even with a local provider; `tagger.zero_shot=false` also disables batch backfill.

`IMPRINT_DATA_DIR` selects persistent storage consistently in Go and Python. `IMPRINT_WORKSPACE` is an operation override used by queued subprocesses; it must name an existing valid workspace. New jobs capture it at submission so a later dashboard switch cannot redirect them. `imprint sync --workspace NAME` selects the workspace for one session the same way, without changing the active workspace. MCP calls resolve their workspace at entry. It does not create persistent per-client/project binding.

See [embedding rebuild instructions](embeddings.md) and [local helper setup](local-models.md). Explicit persisted values override new defaults. Model configuration changes require restarting long-running processes. Local summaries share `tagger.local.*`; local non-thinking and grammar controls do not configure external servers.
