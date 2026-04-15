# Third-Party Licenses

Imprint is licensed under [Apache 2.0](LICENSE). This document lists third-party dependencies and their licenses.

Imprint does **not** vendor or redistribute these dependencies — they are installed by `pip`/`go mod` at user install time, or downloaded at runtime (Qdrant binary, embedding model weights). This file is provided as a courtesy reference for license audits.

## Go Dependencies

From [go.mod](go.mod):

| Package | License | Source |
|---|---|---|
| `nhooyr.io/websocket` | MIT | https://github.com/coder/websocket |

## Python Dependencies

From [requirements.txt](requirements.txt):

| Package | License | Source |
|---|---|---|
| `fastmcp` | Apache 2.0 | https://github.com/jlowin/fastmcp |
| `qdrant-client` | Apache 2.0 | https://github.com/qdrant/qdrant-client |
| `onnxruntime` | MIT | https://github.com/microsoft/onnxruntime |
| `tokenizers` | Apache 2.0 | https://github.com/huggingface/tokenizers |
| `huggingface-hub` | Apache 2.0 | https://github.com/huggingface/huggingface_hub |
| `numpy` | BSD-3-Clause | https://github.com/numpy/numpy |
| `chonkie` | MIT | https://github.com/chonkie-inc/chonkie |
| `model2vec` | MIT | https://github.com/MinishLab/model2vec |
| `tree-sitter-language-pack` | MIT | https://github.com/Goldziher/tree-sitter-language-pack |
| `anthropic` | MIT | https://github.com/anthropics/anthropic-sdk-python |
| `openai` | Apache 2.0 | https://github.com/openai/openai-python |

Transitive dependencies inherit these upstream licenses — consult each project's own manifest.

## Runtime-Downloaded Binaries

| Component | License | Download source |
|---|---|---|
| Qdrant server binary (`v1.17.1` default) | Apache 2.0 | https://github.com/qdrant/qdrant/releases |

Downloaded by [`imprint/qdrant_runner.py`](imprint/qdrant_runner.py) on first MCP/CLI call into `data/qdrant-bin/`. Not bundled with the imprint source distribution or Docker image.

## Runtime-Downloaded Model Weights

| Model | License | Source |
|---|---|---|
| **EmbeddingGemma-300M** (default) | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) + [Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy) | https://huggingface.co/google/embeddinggemma-300m (ONNX: https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX) |
| BGE-M3 (alternative) | MIT | https://huggingface.co/BAAI/bge-m3 |
| Model2Vec static embedder (used by chunker) | MIT | https://huggingface.co/minishlab |

**Gemma is not Apache/MIT.** Users accept the Gemma Terms when downloading weights from HuggingFace. Imprint does not redistribute Gemma weights in source or Docker images. Switch to a differently-licensed model via `imprint config set model.name <repo>`.

## User-Supplied Services

These services are called via their official SDKs with user-supplied API keys. Their TOS binds the user directly, not imprint:

- **Anthropic API** — https://www.anthropic.com/legal/commercial-terms
- **OpenAI API** — https://openai.com/policies/terms-of-use
- **Google Gemini API** — https://ai.google.dev/gemini-api/terms
- **Ollama / vLLM** — self-hosted, user's own deployment

## Attribution Obligations Summary

| License | Obligation met by |
|---|---|
| MIT / BSD-3-Clause | This file lists the packages + upstream repo URLs (where full LICENSE texts live) |
| Apache 2.0 | Same + no modification to upstream code, no NOTICE redistribution required since we don't redistribute source |
| Gemma Terms | Pass-through via HuggingFace click-through; noted in [README](README.md) and [docs/embeddings.md](docs/embeddings.md) |

If you redistribute imprint in a form that *bundles* any of the above (e.g. a prebuilt Docker image with Qdrant baked in, or a distribution that includes Gemma weights), you inherit that dependency's attribution obligations and must include its LICENSE/NOTICE in your distribution.
