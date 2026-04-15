# Installation

## Requirements

**Core:**
- Python 3.9+
- pip
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)

**Optional — GPU acceleration:**
- NVIDIA GPU + CUDA 12 + `onnxruntime-gpu` for ~20× faster embedding (see [embeddings.md](./embeddings.md))

**LLM topic tagging** (`IMPRINT_LLM_TAGS=1`) — `anthropic` and `openai` SDKs are installed automatically with `imprint setup`. No extra steps needed for any provider.

## Quick Install

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex
```

This clones the repo, builds the binary, installs Python dependencies (including ONNX Runtime + Qdrant client + Chonkie), registers the MCP server, configures Claude Code hooks, and sets up shell aliases. One command, everything ready.

## Install a specific version or channel

The default installer resolves `/releases/latest`, which GitHub maps to the most recent **stable** (non-prerelease) build. Two release channels are published:

| Channel | Tag pattern | Produced by |
|---|---|---|
| **stable** | `vX.Y.Z` | conventional-commit release on every merge to `main` |
| **dev** | `vX.Y.Z-dev.N` | prerelease on every push to `dev` (N = build number) |

Pin a specific release or switch channels via env var or CLI flag.

**Linux / macOS:**
```bash
# Pin to a specific tag (env var)
IMPRINT_VERSION=v0.2.0 curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash

# Pin via CLI arg
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --version v0.2.0

# Latest dev prerelease
IMPRINT_CHANNEL=dev curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.sh | bash -s -- --dev
```

**Windows (PowerShell):**
```powershell
# Pin to a specific tag
$env:IMPRINT_VERSION = "v0.2.0"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Latest dev prerelease
$env:IMPRINT_CHANNEL = "dev"; irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 | iex

# Or download and pass args directly
irm https://raw.githubusercontent.com/alexandruleca/imprint-memory-layer/main/install.ps1 -OutFile install.ps1
.\install.ps1 -Version v0.2.0
.\install.ps1 -Dev
```

Precedence: CLI flag > `IMPRINT_VERSION` env > `IMPRINT_CHANNEL=dev` env > default stable. The installer `git checkout`s the matching tag so the repo source tree aligns with the binary version.

## Run the relay server (Docker)

Prebuilt multi-arch images (`linux/amd64`, `linux/arm64`) are published to GHCR on every release:

```bash
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:latest        # latest stable
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:v0.2.0         # pinned
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:v0.2           # floating minor
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:dev            # latest dev
```

Or deploy to Docker Swarm with [`docker-compose.relay.yml`](../docker-compose.relay.yml) (includes Traefik labels for HTTPS termination).

See [sync.md](./sync.md) for how peer sync uses the relay.
