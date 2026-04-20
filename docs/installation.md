---
title: Installation
description: Install Imprint on macOS, Linux, or Windows in 30 seconds — one-line installer, version pinning, update command, Docker alternative, and upgrade safety.
---

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

## Supported hosts

`imprint setup all` probes for each of the following and wires whichever it finds. Missing tools are skipped cleanly:

| Target | Config path | Notes |
|---|---|---|
| `claude-code` | `~/.claude/` | Claude Code CLI (stdio MCP + hooks). |
| `claude-desktop` | macOS `~/Library/Application Support/Claude/` · Windows `%APPDATA%\Claude\` or `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\` (MS Store install) | Anthropic's desktop app. Detects both the standalone and Microsoft Store (MSIX) install layouts. **WSL2-aware**: when run from a WSL shell, writes the config to the Windows-side `claude_desktop_config.json` and wires the command through `wsl.exe -d <distro> -- env <PYTHONPATH=...> <venv-python> -m imprint`, so the Windows app launches the Linux venv directly. No second Windows-side install needed. |
| `chatgpt-desktop` | — | Detection-only. ChatGPT Desktop wires MCP via in-app hosted connectors (SSE), not a local stdio config, so `setup` reports the install and points at the `supergateway` reverse-proxy pattern. |
| `cursor` | `~/.cursor/` | MCP + hooks + always-on rule. |
| `codex` | `~/.codex/` | Codex CLI (TOML config). |
| `copilot` | VS Code settings | Custom instructions + `mcp.json`. |
| `cline` | `~/.clinerules/` + Cline extension + CLI | Rules file + MCP entries in both the VS Code extension and the Cline CLI. |
| `openclaw` | `~/.openclaw/openclaw.json` | Also recognized as `clawdbot` / `moltbot`. Nested `mcp.servers` key. |

Target a specific host with `imprint setup <target>` (e.g. `imprint setup claude-desktop`).

### Auto-syncing past conversations from Claude Desktop / ChatGPT Desktop

Neither consumer app keeps conversation content in a stable on-disk format — both load chats from the vendor's server on demand. The supported path is the built-in **export**:

- **Claude Desktop / claude.ai** — Settings → Privacy → Export data. Emails a zip.
- **ChatGPT Desktop / chat.openai.com** — Settings → Data controls → Export. Emails a zip.

Once the zip lands in your Downloads folder, run:

```bash
imprint learn --desktop
```

What it does:

- Scans common Downloads locations (WSL-aware: both `~/Downloads` on Linux and `/mnt/c/Users/<user>/Downloads` on the Windows side).
- Detects Claude / ChatGPT exports by sniffing the zip contents (fingerprint files like `projects.json`, `message_feedback.json`), not just filenames.
- Runs each new zip through the existing JSON extractor — one searchable memory per conversation, split into chunks, tagged.
- Dedups by SHA-256 of the zip; re-runs are cheap and skip anything already indexed.
- Stores under projects `claude-desktop-convos` and `chatgpt-desktop-convos`.

Keep it running in the background while you request new exports:

```bash
imprint learn --desktop --watch            # polls every 30 s by default
imprint learn --desktop --interval 300     # every 5 min
imprint learn --desktop --path ~/Archive   # extra root(s) in addition to Downloads
```

`imprint learn` on its own still indexes Claude Code transcripts and auto-memory files exactly as before; `--desktop` is additive.

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

## Updating an existing install

Once installed, upgrade in place with the CLI — no curl pipe, no sudo, and no chance of clobbering your indexed memory:

```bash
imprint update                 # latest stable (asks for confirmation)
imprint update --dev           # latest prerelease
imprint update --version v0.3.1
imprint update --check         # prints current + latest and exits
imprint update -y              # skip confirmation, for scripts / CI
```

**What's preserved:** `data/` (workspaces, Qdrant storage, SQLite graphs, `config.json`, `workspace.json`, `gpu_state.json`) and `.venv/` (Python virtual environment). Everything else under the install dir (`~/.local/share/imprint/` by default) is replaced with the new release's tree; stale files from the previous release are removed (`rsync --delete-during`). The previous binary is kept at `bin/imprint.prev` in case you need to roll back manually.

Re-running `install.sh` against an existing install also still works, but it now requires confirmation:

```bash
# Interactive (TTY): prompts "Upgrade existing install? [y/N]"
bash install.sh

# Non-interactive (curl | bash): must opt in explicitly
IMPRINT_ASSUME_YES=1 curl -fsSL .../install.sh | bash
curl -fsSL .../install.sh | bash -s -- --yes
```

### Sticky GPU failure handling

`imprint setup` probes your GPU stack every run. When `onnxruntime-gpu` / `llama-cpp-python` fail to produce a working CUDA build (for example: Blackwell `sm_120` with an older `nvcc`, or `libcublasLt.so.12` missing when ORT ships CUDA 12 wheels against a CUDA 13 host), the failure is recorded in `data/gpu_state.json` keyed on `{gpu, nvcc, compute_cap, driver}`. Subsequent `imprint setup` runs skip the broken path silently so you don't see the same multi-minute rebuild warning on every invocation.

After you upgrade your CUDA toolkit or driver, force a retry:

```bash
imprint setup --retry-gpu
```

The setup also auto-installs the `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `nvidia-cufft-cu12`, and `nvidia-curand-cu12` pip wheels into the venv when a smoke test detects a missing CUDA runtime library — so the common "ORT lists `CUDAExecutionProvider` but sessions can't be created" case is fixed without manual intervention.

## Run the relay server (Docker)

Prebuilt multi-arch images (`linux/amd64`, `linux/arm64`) are published to GHCR
after every release:

```bash
docker run -p 8430:8430 ghcr.io/alexandruleca/imprint-relay:latest
```

Full deployment guide — pinned tags, `docker compose`, TLS termination (Caddy /
Traefik / Nginx), health checks, and manual rebuilds — lives on the dedicated
[Docker page](./docker.md). See [sync.md](./sync.md) for how peer sync uses the
relay.
