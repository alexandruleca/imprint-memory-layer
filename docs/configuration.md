# Configuration

All settings can be persisted via `imprint config` instead of setting environment variables. Settings are stored in `data/config.json` (gitignored).

**Precedence:** env var > config.json > hardcoded default. Environment variables always win, so you can override config.json for one-off runs.

```bash
# Switch to a different embedding model
imprint config set model.name nomic-ai/nomic-embed-text-v2-moe
imprint config set model.dim 768
imprint config set model.seq_length 512

# Use local Ollama for LLM tagging
imprint config set tagger.llm true
imprint config set tagger.llm_provider ollama
imprint config set tagger.llm_model llama3.2

# Custom Qdrant server
imprint config set qdrant.host 192.168.1.50
imprint config set qdrant.no_spawn true

# See what's changed
imprint config

# One-off env override (doesn't persist)
IMPRINT_DEVICE=gpu imprint ingest ~/code

# Reset everything
imprint config reset --all
```

## All Settings

| Key | Default | Description |
|-----|---------|-------------|
| **Embedding model** | | |
| `model.name` | `onnx-community/embeddinggemma-300m-ONNX` | HuggingFace embedding model repo |
| `model.file` | `auto` | ONNX model file (auto = pick by device) |
| `model.device` | `auto` | Compute device: auto / cpu / gpu |
| `model.dim` | `768` | Embedding vector dimension |
| `model.seq_length` | `2048` | Token cap per embed call |
| `model.threads` | `4` | CPU intra-op threads for ONNX |
| `model.gpu_mem_mb` | `2048` | VRAM cap for ORT CUDA arena (MB) |
| `model.gpu_device` | `0` | CUDA device ID |
| `model.pooling` | `auto` | Pooling strategy: auto / cls / mean / last |
| **Qdrant** | | |
| `qdrant.host` | `127.0.0.1` | Qdrant bind/connect host |
| `qdrant.port` | `6333` | Qdrant HTTP port |
| `qdrant.grpc_port` | `6334` | Qdrant gRPC port |
| `qdrant.version` | `v1.17.1` | Pinned Qdrant release for auto-download |
| `qdrant.no_spawn` | `false` | Skip auto-spawn (BYO server) |
| **Chunker** | | |
| `chunker.overlap` | `400` | Sliding overlap chars between chunks |
| `chunker.size_code` | `4000` | Soft target chunk size for code |
| `chunker.size_prose` | `6000` | Soft target chunk size for prose |
| `chunker.hard_max` | `8000` | Absolute max chunk size |
| `chunker.semantic_threshold` | `0.5` | Topic-shift threshold (lower = sharper splits) |
| **Tagger** | | |
| `tagger.zero_shot` | `true` | Enable zero-shot topic tagging |
| `tagger.llm` | `false` | Enable LLM topic tagging (replaces zero-shot) |
| `tagger.llm_provider` | `anthropic` | LLM provider: anthropic / openai / ollama / vllm / gemini |
| `tagger.llm_model` | `claude-haiku-4-5` | LLM tagger model name |
| `tagger.llm_base_url` | — | LLM tagger API base URL override |
| **Other** | | |
| `collection` | `memories` | Default Qdrant collection name |

Each setting also has an `IMPRINT_*` env var (shown via `imprint config get <key>`). Env vars still work and take priority over config.json.
