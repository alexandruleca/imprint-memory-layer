---
title: Docker
---

# Docker

Two images are published to GHCR on every release:

| Image | What it runs | When to use |
|---|---|---|
| `ghcr.io/alexandruleca/imprint` | Dashboard UI + API on :8420, auto-Qdrant, CLI baked in | You want a containerized Imprint dev environment, or you want to expose the dashboard to your team without touching your host |
| `ghcr.io/alexandruleca/imprint-relay` | Stateless WebSocket forwarder for peer sync on :8430 | You want to self-host the sync relay behind your own domain |

Both are multi-arch (`linux/amd64`, `linux/arm64`). Same tag scheme: `latest`,
pinned `vX.Y.Z`, floating `vX.Y`, plus `dev` / `vX.Y.Z-dev.N` for the
prerelease channel.

## Quick start — full Imprint runtime

```bash
docker run -d \
  --name imprint \
  --restart unless-stopped \
  -p 8420:8420 \
  -v imprint-data:/data \
  ghcr.io/alexandruleca/imprint:latest
```

Then browse `http://localhost:8420` for the dashboard. The first request takes
longer than subsequent ones — Imprint downloads the EmbeddingGemma-300M model
(~800 MB) and the Qdrant binary (~50 MB) into `/data` on first use, then
everything is local. Mount `/data` as a volume so the cache + Qdrant storage
survives container restarts.

### Ingest content from the host

The container bind-mounts well — point `/workspace` at the repo you want
indexed and drive the CLI via `docker exec`:

```bash
docker run -d \
  --name imprint \
  -p 8420:8420 \
  -v imprint-data:/data \
  -v "$PWD:/workspace:ro" \
  ghcr.io/alexandruleca/imprint:latest

# Ingest the mounted repo:
docker exec -it imprint imprint ingest /workspace

# Check status:
docker exec -it imprint imprint status
```

### Ingest content from an API

For pushing arbitrary data into memory from an external service, use the
`ingest_content` MCP tool (see [MCP tools](./mcp.md)). If your service
already speaks MCP, point it at the container and call:

```json
{
  "tool": "ingest_content",
  "arguments": {
    "content": "...your text, CSV, JSON, or code...",
    "name": "user-upload-2026-04-20",
    "format": "csv",
    "project": "uploads"
  }
}
```

Re-sending the same `name` replaces the prior chunks for that source, so the
tool is safe for idempotent retries. `format` accepts `text`, `markdown`,
`csv`, `json`, or `code:<lang>` (e.g. `code:python`, `code:typescript`).

### `docker compose`

The [`docker-compose.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/docker-compose.yml)
at the repo root defines both services. Default `docker compose up` starts
only the Imprint runtime:

```bash
docker compose up -d                  # imprint on :8420
docker compose logs -f imprint
```

Add the relay with the `relay` profile:

```bash
docker compose --profile relay up -d  # imprint + relay
```

Pin a specific channel by editing the `image:` lines:

```yaml
services:
  imprint:
    image: ghcr.io/alexandruleca/imprint:v0.3.0   # pinned stable
    # image: ghcr.io/alexandruleca/imprint:dev     # latest dev prerelease
```

### What ships inside the image

- Imprint Go CLI (`imprint ingest`, `imprint status`, `imprint workspace`, …)
- Python package (`imprint.api` dashboard, `python -m imprint` MCP server)
- All of `requirements.txt` **minus** `llama-cpp-python` (the `local` chat
  provider requires a C++ toolchain at build time; it's omitted to keep the
  image slim). Remote chat providers — OpenAI, Anthropic, Ollama, vLLM,
  Gemini — still work because `chat.py` imports `llama_cpp` lazily.

### What's lazy (first-run downloads)

- Qdrant binary (v1.17.x, ~50 MB) — written to `/data/qdrant-bin/`
- EmbeddingGemma-300M ONNX model (~800 MB) — written to `/data/hf-cache/`
  via `HF_HOME`

Total first-run network usage is ~900 MB. Pre-warm by invoking a search or
ingest inside the container before handing it to your team.

### MCP from inside the container

The MCP server speaks stdio. To bridge from a host-side IDE:

```bash
docker exec -i imprint python -m imprint
```

Point your IDE's MCP config at that command. Most users prefer to install
Imprint locally via [install.sh](./installation.md) for the MCP side and use
the container only for the dashboard / API / bulk ingest from external
services.

## Quick start — sync relay only

If you just want to self-host the sync relay (not the full Imprint runtime),
use the smaller relay-only image:

```bash
docker run -d \
  --name imprint-relay \
  --restart unless-stopped \
  -p 8430:8430 \
  ghcr.io/alexandruleca/imprint-relay:latest
```

The relay is stateless — no volume required. See [sync.md](./sync.md) for how
peers authenticate through it.

Point clients at your host:

```bash
imprint sync serve --relay ws://your-host:8430
imprint sync ws://your-host:8430/<room-id> --pin <PIN>
```

## Tags

Available tags on both images:

| Tag | Channel | Example |
|---|---|---|
| `latest` | stable (most recent `vX.Y.Z`) | `ghcr.io/alexandruleca/imprint:latest` |
| `vX.Y.Z` | pinned stable | `ghcr.io/alexandruleca/imprint:v0.3.0` |
| `vX.Y` | floating stable minor | `ghcr.io/alexandruleca/imprint:v0.3` |
| `dev` | most recent prerelease | `ghcr.io/alexandruleca/imprint:dev` |
| `vX.Y.Z-dev.N` | pinned prerelease | `ghcr.io/alexandruleca/imprint:v0.3.0-dev.4` |

## TLS termination

Neither image terminates TLS — both speak plain HTTP/WS. Put a reverse proxy
in front for production. TLS matters most for the relay since browsers require
`wss://` for WebSocket upgrades.

### Caddy (easiest)

```caddyfile
imprint.yourdomain.com {
    reverse_proxy localhost:8420
}

sync.yourdomain.com {
    reverse_proxy localhost:8430
}
```

Caddy auto-provisions Let's Encrypt certs for both.

### Docker Swarm + Traefik (relay)

For swarm deployments of just the relay, [`docker-compose.relay.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/docker-compose.relay.yml)
includes Traefik labels with a `letsencrypt` cert resolver:

```bash
docker stack deploy -c docker-compose.relay.yml imprint
```

Edit the `Host(...)` rule to match your domain before deploying.

### Nginx (relay)

```nginx
server {
    listen 443 ssl http2;
    server_name sync.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/sync.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.yourdomain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8430;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_read_timeout 3600s;   # long-lived sessions
        proxy_send_timeout 3600s;
    }
}
```

## Health & observability

- **imprint** — healthcheck hits `GET /api/version`. `docker logs -f imprint`
  tails the FastAPI / Qdrant output.
- **imprint-relay** — healthcheck hits `GET /health` (returns
  `{"status":"ok","rooms":N}`).

## Building from source

```bash
git clone https://github.com/alexandruleca/imprint-memory-layer.git
cd imprint-memory-layer

# Full Imprint runtime:
docker build --build-arg VERSION=dev -f Dockerfile.imprint -t imprint:local .

# Sync relay:
docker build --build-arg VERSION=dev -f Dockerfile -t imprint-relay:local .
```

## Release pipeline

Both images are built by [`image.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/.github/workflows/image.yml)
— a dedicated workflow that fires **after** the release workflow succeeds via
`workflow_run`. It runs a matrix of two builds (relay + imprint) so either
can be rebuilt independently. Manual rebuilds of a specific tag are available
via `workflow_dispatch`, useful for rebasing an image without cutting a new
release.
