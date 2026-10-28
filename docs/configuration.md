---
title: Configuration
description: Tune Imprint — model, chunker, tagger, Qdrant, and queue settings via `imprint config set`, with environment-variable overrides and precedence rules.
---
All settings can be persisted via `imprint config` instead of setting environment variables. Settings are stored in `data/config.json` (gitignored).

**Precedence:** env var > config.json > hardcoded default. Environment variables always win, so you can override config.json for one-off runs.

Project association settings (also in **Settings → Project associations**):

| Key | Default | Meaning |
| --- | --- | --- |
| `associations.enabled` | `true` | Suggest links after a full snapshot is published; existing manual links remain active when disabled. |
| `associations.auto_accept_paths` | `false` | Accept exact registered-project-path matches automatically. Name and local-model matches still need review. |
| `associations.unassigned_workspace` | empty / Off | Explicit known workspace for client conversations without cwd evidence. Never overrides conflicting directory ownership. |

Environment overrides are `IMPRINT_ASSOCIATIONS_ENABLED`,
`IMPRINT_ASSOCIATIONS_AUTO_PATHS` and `IMPRINT_CONVERSATION_INBOX_WORKSPACE`.
See [project organization](workspaces.md#associate-one-source-with-several-projects).

```bash
# Select the supported Qwen default (then rebuild; see embeddings.md)
imprint config set model.name Qwen/Qwen3-Embedding-0.6B-GGUF
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
IMPRINT_DEVICE=cpu imprint ingest ~/code

# Remove one override
imprint config reset model.device

# Wipe all overrides
imprint config reset --all
```

`imprint config` groups settings visually by prefix (`model.*`, `huggingface.*`, `qdrant.*`, `chunker.*`, `tagger.*`, `tagger.local.*`, `ingest.*`, `chat.*`, `summarizer.*`, `recall.*`). Non-default values are highlighted by source (`config.json` cyan, `env` yellow).

The dashboard's **Settings** page edits the same values. A setting with known values, such as `model.device`, a provider or `model.gpu_device` (the GPUs llama.cpp finds on this device), is a dropdown. **Custom value…** accepts anything else the setting allows. A value set by an environment variable is shown read-only. The reset button (remove, for a secret) deletes the `config.json` override, like `imprint config reset <key>`. Both the CLI and the dashboard refuse a value outside a fixed set.

Settings also holds **Workspaces** and, for a Windows installation, **WSL profiles**: enroll or remove the client profiles (whichever clients the CLI supports there) of each WSL distribution's default user. See [installation](installation.md#clients-inside-wsl).

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
| `model.name` | `Qwen/Qwen3-Embedding-0.6B-GGUF` | HuggingFace embedding model repo (GGUF) |
| `model.revision` | `auto` | Pinned default export revision; custom repos use main unless pinned |
| `model.file` | `auto` | GGUF file in the model repo (auto = the default model's file; a custom repo must name its file) |
| `model.device` | `auto` | Compute device: auto / cpu / gpu. `auto` embeds on a GPU when llama.cpp finds one, else on the CPU; `cpu` forces the CPU; `gpu` asks for the GPU and warns, then uses the CPU, when there is none. Any GPU llama.cpp's build supports: Vulkan on Windows and Linux x64, Metal on macOS. Changing it installs nothing; running processes use it once they restart. Settings marks `gpu` unavailable where llama.cpp finds no GPU ([ADR 0086](adr/0086-embeddings-run-on-llama-cpp-without-cuda.md)) |
| `model.dim` | `1024` | Embedding vector dimension |
| `model.seq_length` | `2048` | Token cap per embed call |
| `model.threads` | `0` | CPU threads for CPU embedding and the privacy filter (0 = half the logical CPUs) |
| `model.gpu_device` | `auto` | GPU that runs embeddings and the local LLM, each model whole on it: `auto` = the discrete GPU with the most memory (an integrated GPU only when there is no discrete one), else an index into the GPUs llama.cpp offloads to ([ADR 0087](adr/0087-llama-cpp-runs-each-model-on-one-gpu.md)) |
| `model.batch_size` | `0` | Embedding batch size (0 = auto: 8 on a GPU, 1 on the CPU) |
| `model.pooling` | `auto` | Pooling strategy: auto / cls / mean / last. `auto` follows the model family (Qwen3 last token, BGE-M3 CLS), else the GGUF's own metadata |
| `model.query_instruction` | `auto` | Model-specific query task; Qwen wraps Instruct/Query |
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
| `tagger.llm` | `false` | Enable LLM tagging: memories stored by learn/ingest/refresh are classified by the dashboard's background tagging job ([tagging](tagging.md#llm-tagging-runs-in-the-background)) |
| `tagger.llm_provider` | `local` | LLM provider: anthropic / openai / ollama / vllm / gemini / local |
| `tagger.llm_model` | — | Server model override (default per provider); local uses GGUF settings |
| `tagger.llm_base_url` | — | LLM tagger API base URL override |
| **Tagger — local model (llama-cpp, provider=`local`)** | | |
| `tagger.local.model_repo` | `unsloth/Qwen3.5-4B-GGUF` | HF repo for GGUF auto-download |
| `tagger.local.model_file` | `Qwen3.5-4B-Q4_K_M.gguf` | GGUF filename within the repo |
| `tagger.local.model_path` | — | Absolute path to a local GGUF (overrides repo/file) |
| `tagger.local.n_ctx` | `8192` | Tagger context window in tokens |
| `tagger.local.n_gpu_layers` | `-1` | GPU layers to offload (-1 = all) |
| **Redaction** | | |
| `redact.privacy_filter` | `false` | Also redact the secrets and account numbers OpenAI Privacy Filter finds, after the always-on rules. CPU only, ~837 MB download on first use, seconds per chunk; a write fails rather than store less redacted text when the model cannot run. See [secret redaction](redaction.md) |
| **Ingest (docs + URLs)** | | |
| `ingest.doc_formats` | `pdf,docx,pptx,xlsx,csv,epub,rtf,html,eml,json` | Comma-separated doc formats enabled for the file walker |
| `ingest.ocr_enabled` | `true` | Local OCR for scanned PDFs and images. Setup installs RapidOCR (including its small Chinese/English models), Pillow and the PDFium renderer; no system tools are required. Explicit saved `false` stays off. See [ADR 0076](adr/0076-default-ocr-with-wheel-installed-models-and-renderer.md). |
| `ingest.ocr_lang` | `eng` | `eng` or `auto` uses the bundled Chinese/English recognizer. Other Tesseract codes (e.g. `eng+fra`) use the compatibility backend and require system Tesseract plus the selected language data. |
| `ingest.max_doc_size_mb` | `25` | Per-file byte cap for document extraction |
| **Audio ingestion** | | |
| `audio.enabled` | `true` | Include audio files in ingestion/refresh; the selected model downloads only on first uncached use |
| `audio.model` | `large-v3-turbo` | Multilingual CPU model: `large-v3-turbo` (~1.51 GiB) or `small` (~464 MiB) |
| `audio.language` | `auto` | Detect each window's language, or pin `ro` / `en`; output is transcribed, not translated |
| `audio.max_file_size_mb` | `1024` | Positive per-audio-file size limit in MiB, independent of document limits |
| `audio.max_duration_minutes` | `120` | Positive decoded-audio duration limit in minutes |
| **URL ingestion** | | |
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
| `voice.avatar` | `echo` | Echo's look and voice: `echo` (a synaptic core whose neurons fire as it thinks, around an eye that follows the pointer; a robotic voice), `iris` (indigo straight cut, pale lavender eyes; a female voice) or `kai` (spiky orange hair, a determined frown; a male voice). Also chosen on the Voice page and in Companion settings. See [Echo voice](live-voice.md). |
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
| `chat.research_timeout_sec` | `300` | Research time budget in seconds (10–3600), excluding setup and the reserved final answer. Env: `IMPRINT_CHAT_RESEARCH_TIMEOUT`. |
| `chat.research_token_budget` | `65536` | Prompt + maximum output reservations for all research coordinator/worker requests (1024–2000000). Env: `IMPRINT_CHAT_RESEARCH_TOKENS`. |
| `chat.research_no_progress_rounds` | `4` | Consecutive read rounds without new evidence before synthesis (2–20). Env: `IMPRINT_CHAT_RESEARCH_PATIENCE`. |
| `chat.analysis_workers` | `2` | Logical workers for oversized chat questions/evidence, executed sequentially. `1`–`8` keep separate rolling notes; `0` disables section analysis. Settings → Chat → Sequential analysis workers. Env: `IMPRINT_CHAT_ANALYSIS_WORKERS`. See [local helper models](local-models.md#large-chat-questions-and-evidence). |
| `chat.tool_format` | `auto` | Tool-call format of the local model: `auto` (detected from the GGUF's chat template), `qwen-xml`, `hermes`, `llama3`, `mistral`, or `none` (chat without tools). Remote providers ignore it. See [local helper models](local-models.md). |
| **Session summarizer (opt-in, Stop hook)** | | |
| `summarizer.enabled` | `false` | Enable the LLM-based session summarizer |
| `summarizer.provider` | `local` | Provider: local / ollama / vllm / anthropic / openai / gemini |
| `summarizer.model` | `qwen3.5:4b` | Model name for the chosen provider |
| `summarizer.base_url` | — | API base URL override |
| `summarizer.min_messages` | `5` | Skip sessions with fewer messages |
| `summarizer.max_input_tokens` | `5000` | Truncate transcript before summarizing |
| **Recall (MCP tool)** | | |
| `recall.llm` | `false` | Let the local model plan recall reads and answer from the evidence |
| `recall.llm_timeout_sec` | `30` | Seconds one recall waits for the local model (planning and answering together) |
| **Workspace selection** | | |
| `workspace.unassigned` | — (the active workspace) | Workspace for agent sessions outside an assigned project, such as desktop app chats. Choices are this device's workspaces; deleting the chosen one resets it. Env `IMPRINT_UNASSIGNED_WORKSPACE`. See [workspace per project](workspaces.md#workspace-per-project) |
| **Updates** | | |
| `update.channel` | `stable` | Releases this installation updates to: `stable`, or `dev` for dev builds as well. The dashboard's update notice and `imprint update` follow it; `--stable` or `--dev` overrides it for one run. Env `IMPRINT_UPDATE_CHANNEL`. See [updating an existing install](installation.md#updating-an-existing-install) |
| **Other** | | |
| `collection` | `memories` | Default Qdrant collection name (workspace suffix is appended automatically) |

Research has no fixed tool-round cutoff. It continues while new evidence appears
within the time and token budgets, then reserves a tools-free final answer.
Repeated evidence across different queries, alternating cycles, empty reads and
recurring errors can stop research earlier. Writes are excluded from the
stagnation heuristic. Settings → Chat exposes **Research time budget (seconds)**,
**Research token budget** and **Rounds without new evidence**.

Token accounting conservatively reserves each formatted prompt plus its maximum
output, including section workers and repeated prompt processing. Local prompt
counts use the model tokenizer; remote prompts use an estimate. Unused output
reservations are not refunded; this is not a provider billing or monetary cap.
Time checks are cooperative: an already-running blocking tool or native prompt
prefill may finish after the deadline. Pending calls are then skipped explicitly.
The final answer has separate `chat.max_tokens` space and a 120-second deadline.
If it cannot be synthesized, labelled evidence excerpts are preserved.

The old `chat.max_tool_iters` key and `IMPRINT_CHAT_MAX_TOOL_ITERS` are ignored;
old config entries stay on disk but do not appear in Settings or cut research
off at six or twelve rounds. See [ADR 0102](adr/0102-chat-research-follows-progress-and-resource-budgets.md).

Each setting also has an env var, `IMPRINT_*` except `HF_TOKEN` (shown via `imprint config get <key>`). Env vars still work and take priority over config.json.

## Recall

The `recall` MCP tool ([MCP tools](mcp.md#recall-several-reads-in-one-call)) works without any model. `recall.llm` lets a model plan its reads and condense the evidence into a cited answer, but only a model on this computer:

- `tagger.llm_provider=local`: the GGUF configured under `tagger.local.*`, loaded by llama.cpp inside each MCP server process on its first recall. Recall does not download it; until the file exists (LLM tagging or the dashboard chat downloads the default one), recall notes that the model is missing and runs without it.
- `tagger.llm_provider=ollama` or `vllm`, when `tagger.llm_base_url` (or the provider's default `localhost` URL) points to a loopback address. A server elsewhere on the network does not count.
- Any other provider (`anthropic`, `openai`, `gemini`) leaves recall without a model, with a one-line note. Recall questions and evidence are never sent to a remote API.

`recall.llm` is independent of `tagger.llm`: recall can use the model while ingest tagging stays off. Both use the same provider settings.

`recall.llm_timeout_sec` bounds the time one recall waits for the model, planning and answering together. At the limit recall returns what it returns without the model (the search, or the evidence of the steps that ran) and notes why. llama.cpp stops generating at its next token, but a prompt it is already reading finishes in the background first. On a CPU the default usually lets the model plan but not answer (measured: 1 of 16 answers within 30 s, about 25–45 s per answer with more time); see [benchmarks](benchmarks.md#recall-lookups). Some clients (Codex, for one) default to a 60 s MCP tool timeout, so keep the limit below the client's. Each Claude Code or Codex session runs its own MCP server, so with the `local` provider every session that recalls holds its own copy of the model in memory (about 3 GB for the default Qwen3.5-4B); ollama or vllm share one.

```bash
imprint config set recall.llm true
imprint config set recall.llm_timeout_sec 60   # e.g. CPU-only machines
```

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

`IMPRINT_DATA_DIR` selects persistent storage consistently in Go and Python. `IMPRINT_WORKSPACE` is an operation override used by queued subprocesses; it must name an existing valid workspace. It outranks a directory's own workspace, so set in a client's MCP server environment it pins every session of that client. New jobs capture it at submission so a later dashboard switch cannot redirect them. `imprint sync --workspace NAME` selects the workspace for one session the same way, without changing the active workspace. MCP calls resolve their workspace at entry. Per-project binding is a project file this device approved ([workspace per project](workspaces.md#workspace-per-project), [ADR 0072](adr/0072-projects-name-their-workspace-and-each-device-approves-it.md)).

See [embedding rebuild instructions](embeddings.md) and [local helper setup](local-models.md). Explicit persisted values override new defaults. Model configuration changes require restarting long-running processes. Local summaries share `tagger.local.*`; local non-thinking and grammar controls do not configure external servers.
