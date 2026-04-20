---
title: Docker (self-hosted relay)
---

# Docker — self-hosted relay

The Imprint relay is a small, stateless WebSocket forwarder used by peer sync
to shuttle bytes between two machines behind NAT. A public relay is available
at `wss://imprint.alexandruleca.com` — self-host only if you want your own
domain, your own logs, or an air-gapped deployment. No data is stored on the
relay; all auth (PIN + fingerprint) happens between the peers.

> **Note:** the relay is the only component that ships as a container. The MCP
> memory server runs per-developer-machine over stdio — it isn't a shared
> service, so there's nothing to deploy. Install Imprint on your dev machine
> via [installation](./installation.md) instead.

## Image

Prebuilt multi-arch images (`linux/amd64`, `linux/arm64`) are published to
GHCR. Available tags:

| Tag | Channel | Example |
|---|---|---|
| `latest` | stable (most recent `vX.Y.Z`) | `ghcr.io/alexandruleca/imprint-relay:latest` |
| `vX.Y.Z` | pinned stable | `ghcr.io/alexandruleca/imprint-relay:v0.2.0` |
| `vX.Y` | floating stable minor | `ghcr.io/alexandruleca/imprint-relay:v0.2` |
| `dev` | most recent prerelease | `ghcr.io/alexandruleca/imprint-relay:dev` |
| `vX.Y.Z-dev.N` | pinned prerelease | `ghcr.io/alexandruleca/imprint-relay:v0.2.0-dev.3` |

Image size is small — Alpine base + a single static Go binary, no Python, no
Qdrant, no model weights.

## Quick start

### `docker run`

```bash
docker run -d \
  --name imprint-relay \
  --restart unless-stopped \
  -p 8430:8430 \
  ghcr.io/alexandruleca/imprint-relay:latest
```

Then point your clients at it:

```bash
# On machine A (provider)
imprint sync serve --relay ws://your-host:8430

# On machine B (consumer) — room ID + PIN printed by A
imprint sync ws://your-host:8430/<room-id> --pin <PIN>
```

For production you almost always want TLS (browsers and most `wss://` clients
require it). Put the relay behind a reverse proxy — see [With TLS](#with-tls)
below.

### `docker compose`

The repo ships [`docker-compose.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/docker-compose.yml)
for single-host deployment:

```bash
docker compose up -d
docker compose logs -f imprint-relay
```

It pins `:latest`, exposes `8430`, and enables a `/health` healthcheck.
Change the pinned tag if you want a specific channel:

```yaml
services:
  imprint-relay:
    image: ghcr.io/alexandruleca/imprint-relay:v0.2.0   # pinned
    # image: ghcr.io/alexandruleca/imprint-relay:dev     # latest dev
```

## With TLS

The relay only speaks plain HTTP/WS. Terminate TLS at a reverse proxy. Two
common setups:

### Caddy (easiest)

```caddyfile
sync.yourdomain.com {
    reverse_proxy localhost:8430
}
```

Caddy auto-provisions a Let's Encrypt certificate. Clients then use:

```bash
imprint sync serve --relay sync.yourdomain.com
# scheme auto-detects to wss:// for non-localhost hosts
```

### Docker Swarm + Traefik

For swarm deployments, [`docker-compose.relay.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/docker-compose.relay.yml)
includes Traefik labels for HTTPS termination with a `letsencrypt` cert
resolver:

```bash
docker stack deploy -c docker-compose.relay.yml imprint
```

Edit the `Host(...)` rule to match your domain before deploying.

### Nginx

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

The relay exposes `/health` which returns `{"status":"ok","rooms":<n>}`. Use it
for uptime probes or load balancer health checks. Logs go to stdout — tail them
with `docker logs -f imprint-relay`.

## Configuration

The relay is deliberately minimal. The only knobs:

| Flag / env | Default | Meaning |
|---|---|---|
| `--port <N>` | `8430` | Listen port |
| `PORT` env | — | Same as `--port` (for platforms like Fly.io / Heroku) |

Rooms expire after 1 hour of inactivity — not configurable; just start a new
session.

## Building from source

The `Dockerfile` is at the repo root:

```bash
git clone https://github.com/alexandruleca/imprint-memory-layer.git
cd imprint-memory-layer
docker build --build-arg VERSION=dev -t imprint-relay:local .
docker run -p 8430:8430 imprint-relay:local
```

## Release pipeline

Images are built by a dedicated
[`image.yml`](https://github.com/alexandruleca/imprint-memory-layer/blob/main/.github/workflows/image.yml)
workflow that runs **after** the release workflow completes (`workflow_run`
trigger). A stable tag push → `:latest`, `:vX.Y.Z`, `:vX.Y`. A dev push → `:dev`,
`:vX.Y.Z-dev.N`. The image build can also be triggered manually via
`workflow_dispatch` if you need to rebuild an old tag without cutting a new
release.
