# Third-Party Licenses

Imprint is licensed under [Apache 2.0](LICENSE). This document lists third-party dependencies and their licenses.

Most dependencies are installed by `uv`/`go mod` at user install time or downloaded at runtime (Qdrant binary, Python interpreter, embedding model weights). The **one component redistributed in binary form** inside every release archive is Astral's `uv` — see [Bundled Binaries](#bundled-binaries) below. This file is provided as a courtesy reference for license audits.

## Go Dependencies

From [go.mod](go.mod):

| Package | License | Source |
|---|---|---|
| `nhooyr.io/websocket` | MIT | https://github.com/coder/websocket |

## Python Dependencies

Split across [requirements/base.txt](requirements/base.txt) (always installed), [requirements/gpu.txt](requirements/gpu.txt) (profile=gpu), and [requirements/llm.txt](requirements/llm.txt) (opt-in with `--with-llm`):

| Package | License | Profile | Source |
|---|---|---|---|
| `fastmcp` | Apache 2.0 | base | https://github.com/jlowin/fastmcp |
| `qdrant-client` | Apache 2.0 | base | https://github.com/qdrant/qdrant-client |
| `onnxruntime` | MIT | base (CPU) | https://github.com/microsoft/onnxruntime |
| `onnxruntime-gpu` | MIT | gpu | https://github.com/microsoft/onnxruntime |
| `onnx` | Apache 2.0 | gpu | https://github.com/onnx/onnx |
| `tokenizers` | Apache 2.0 | base | https://github.com/huggingface/tokenizers |
| `huggingface-hub` | Apache 2.0 | base | https://github.com/huggingface/huggingface_hub |
| `numpy` | BSD-3-Clause | base | https://github.com/numpy/numpy |
| `chonkie` | MIT | base | https://github.com/chonkie-inc/chonkie |
| `model2vec` | MIT | base | https://github.com/MinishLab/model2vec |
| `tree-sitter-language-pack` | MIT | base | https://github.com/Goldziher/tree-sitter-language-pack |
| `anthropic` | MIT | base | https://github.com/anthropics/anthropic-sdk-python |
| `openai` | Apache 2.0 | base | https://github.com/openai/openai-python |
| `llama-cpp-python` | MIT | llm (opt-in) | https://github.com/abetlen/llama-cpp-python |

Document extractors (`pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `ebooklib`, `striprtf`, `beautifulsoup4`, `httpx`, `trafilatura`), the FastAPI stack (`fastapi`, `uvicorn`), and optional OCR deps carry permissive licenses (MIT / BSD / Apache 2.0) — see each project's manifest for the exact terms. Transitive dependencies inherit their upstream licenses.

## Bundled Binaries

Shipped inside every `imprint-<os>-<arch>.{tar.gz,zip}` release archive at `bin/uv[.exe]`:

| Component | License | Source | Pinned via |
|---|---|---|---|
| `uv` (Astral) | Apache 2.0 **OR** MIT (dual) | https://github.com/astral-sh/uv | `UV_VERSION` in [Makefile](Makefile) / [scripts/fetch-uv.sh](scripts/fetch-uv.sh) |

uv is a statically-linked Rust binary that provisions Imprint's Python venv and installs wheels at first run. Because it is redistributed in binary form, downstream consumers of an Imprint release archive inherit uv's dual Apache-2.0-or-MIT grant. Full license texts are shipped inside every release archive at `licenses/uv/LICENSE-APACHE` and `licenses/uv/LICENSE-MIT` (mirrored from https://github.com/astral-sh/uv at the pinned `UV_VERSION`). No NOTICE file is required (uv's upstream ships none of substance).

## Runtime-Downloaded Binaries

Fetched on demand into the user's local data dir; not present in Imprint's release archive or Docker image:

| Component | License | Download source | Fetched by |
|---|---|---|---|
| Qdrant server binary (`v1.17.1` default) | Apache 2.0 | https://github.com/qdrant/qdrant/releases | [`imprint/qdrant_runner.py`](imprint/qdrant_runner.py) |
| CPython 3.12 interpreter | PSF License 2.0 (upstream) + MIT (python-build-standalone patches) | https://github.com/astral-sh/python-build-standalone/releases | `uv python install` on first `imprint bootstrap` |

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

| License | Applies to | Obligation met by |
|---|---|---|
| MIT / BSD-3-Clause | Python deps, `uv` (MIT branch), nhooyr/websocket | This file lists the packages + upstream repo URLs (where full LICENSE texts live) |
| Apache 2.0 | Python deps, `uv` (Apache branch), Qdrant | Same + no modification to upstream code. uv is the only Apache-2.0 component we *redistribute* in binary form; we elect the MIT branch of its dual license to avoid the NOTICE redistribution requirement, but honor either at the recipient's choice |
| PSF License 2.0 | CPython (downloaded by uv on first run) | Pass-through; CPython is not redistributed by Imprint — `uv` fetches it from python-build-standalone on first bootstrap |
| Gemma Terms | EmbeddingGemma weights | Pass-through via HuggingFace click-through; noted in [README](README.md) and [docs/embeddings.md](docs/embeddings.md) |

If you redistribute imprint in a form that *bundles* additional components (e.g. a prebuilt Docker image with Qdrant baked in, a distribution that includes Gemma weights, or a snapshot archive that includes the provisioned `.venv/`), you inherit those components' attribution obligations and must include the corresponding LICENSE/NOTICE files in your distribution.
