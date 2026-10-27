---
title: Docker
description: Deploy Imprint with Docker — full runtime (dashboard + API + auto-Qdrant) and stateless sync relay. Pinned tags, compose files, TLS termination, and health checks.
---
The lightweight relay image runs as the unprivileged `imprint` user and probes `/health` with a Docker healthcheck. Hosted CI verifies both effective user and HTTP health. The all-in-one image has its own non-root user and API health probe.

Two images are published to GHCR on every release:

| Image | What it runs | When to use |
|---|---|---|
| `ghcr.io/alexandruleca/imprint` | Dashboard UI + API on :8420, auto-Qdrant, CLI baked in | You want a containerized Imprint dev environment, with local loopback access |
| `ghcr.io/alexandruleca/imprint-relay` | Stateless WebSocket forwarder for peer sync on :8430 | You want to self-host the sync relay behind your own domain |

Both are multi-arch (`linux/amd64`, `linux/arm64`). Same tag scheme: `latest`,
pinned `vX.Y.Z` and floating `vX.Y`. Images are published after stable releases
only; dev prereleases publish none ([ADR 0066](adr/0066-dev-prereleases-from-the-dev-branch.md)).
The `dev` and `vX.Y.Z-dev.N` tags that remain come from earlier dev releases.

## Quick start — full Imprint runtime

```bash
docker run -d \
  --name imprint \
  --restart unless-stopped \
  -p 127.0.0.1:8420:8420 \
  -v imprint-data:/data \
  ghcr.io/alexandruleca/imprint:latest
```

Then browse `http://localhost:8420` for the dashboard. The first request takes
longer than subsequent ones — Imprint downloads the Qwen3-Embedding-0.6B model
(~800 MB) and the Qdrant binary (a 33–35 MB download, 96 MB unpacked on
x86-64) into `/data` on first use, then everything is local. Mount `/data` as a volume so the cache + Qdrant storage
survives container restarts.

### Ingest content from the host

The container bind-mounts well — point `/workspace` at the repo you want
indexed and drive the CLI via `docker exec`:

```bash
docker run -d \
  --name imprint \
  -p 127.0.0.1:8420:8420 \
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
    # image: ghcr.io/alexandruleca/imprint:v0.3    # latest v0.3.x
```

### What ships inside the image

- Imprint Go CLI (`imprint ingest`, `imprint status`, `imprint workspace`, …)
- Python package (`imprint.api` dashboard, `python -m imprint` MCP server)
- The base requirements with the CPU ONNX Runtime (OCR, privacy filter), plus
  `llama-cpp-python` from upstream's prebuilt Linux CPU wheel (ADR 0035), which
  runs embeddings, the tagger and the `local` chat provider (ADR 0086). The
  container embeds on the CPU. Remote chat providers (OpenAI, Anthropic,
  Ollama, vLLM, Gemini) need no local model.

### What's lazy (first-run downloads)

- Qdrant binary (v1.17.x, 96 MB unpacked on x86-64) — written to `/data/qdrant-bin/`
- Qwen3-Embedding-0.6B Q8_0 GGUF (639 MB) — written to `/data/models/embeddings/`
  via `HF_HOME`
- Qwen3.5-4B Q4_K_M GGUF (~2.7 GB) — written to `/data/models/` by the first
  local chat only

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

The relay is stateless — no volume required. `/health` reports status, release, open rooms and the sessions relayed since start for health checks, deploys and the website's live status (any origin may read it); any other plain HTTP request gets the page that explains the relay. Add `--ga4-id G-XXXXXXXXXX` to the relay command to count visits to that page in Google Analytics. Without it the page loads no scripts. See [sync.md](./sync.md) for how
peers authenticate through it.

Point clients at your host:

```bash
imprint sync serve --relay ws://your-host:8430
imprint sync ws://your-host:8430/<code>   # the line serve prints
```

## Tags

Available tags on both images:

| Tag | Channel | Example |
|---|---|---|
| `latest` | stable (most recent `vX.Y.Z`) | `ghcr.io/alexandruleca/imprint:latest` |
| `vX.Y.Z` | pinned stable | `ghcr.io/alexandruleca/imprint:v0.3.0` |
| `vX.Y` | floating stable minor | `ghcr.io/alexandruleca/imprint:v0.3` |

## Dashboard access boundary

The dashboard validates loopback Host and Origin and has no authenticated remote mode. The container entry point uses `--container-bind` to listen on its network namespace and explicitly accept Docker NAT peers; publish the host port on `127.0.0.1` and keep its container network trusted. Unlike the normal native launcher, container mode cannot require loopback peer addresses. A remote client can forge local Host/Origin headers, so publishing `8420:8420` on all host interfaces is unsafe. Qdrant HTTP/gRPC stay on container loopback and are not published. The dashboard cannot be exposed through an arbitrary remote domain using the relay TLS examples below. TLS alone is not authentication. The full image now builds its dashboard from source and installs a Python 3.12 venv from the hashed CPU lock; CLI and API share `/data`.

## TLS termination (relay)

The relay speaks plain WS; put it behind a TLS reverse proxy for remote use. Browsers require `wss://` from HTTPS pages. These examples apply to the relay, not remote dashboard access.

### Caddy (easiest)

```caddyfile
sync.yourdomain.com {
    reverse_proxy localhost:8430
}
```

Caddy auto-provisions a Let's Encrypt certificate for the relay domain.

### Docker Swarm + Traefik (relay)

For swarm deployments of just the relay, [`docker-compose.relay.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/docker-compose.relay.yml)
includes Traefik labels with a `letsencrypt` cert resolver:

```bash
docker stack deploy -c docker-compose.relay.yml imprint
```

Edit the `Host(...)` rule to match your domain before deploying. Updates start
the new relay before stopping the old one and roll back if it never turns
healthy, so an automated redeploy (below) cannot leave the relay down. Rooms
live in the relay's memory: sessions running during an update are cut and have
to be started again.

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
  `{"status":"ok","version":"vX.Y.Z","rooms":N,"sessions":N,"since":"<start time>"}`).

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

After a stable build, the `deploy-relay` job redeploys the public relay: it
POSTs the URL in the repository secret `RELAY_DEPLOY_WEBHOOK` (a Portainer
service webhook on the Swarm service running `imprint-relay:latest`, which
re-pulls that tag) and waits up to five minutes for
`https://imprint.alexandruleca.com/health` to report the new version. A
self-hosted relay can be wired the same way: enable the service webhook in
Portainer and POST it after pushing the image.
