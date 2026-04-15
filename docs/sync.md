# Peer Sync & Visualization

## Peer Sync

```mermaid
sequenceDiagram
    participant A as Machine A
    participant R as Relay Server
    participant B as Machine B

    A->>R: WebSocket connect (provider, room=abc123)
    Note over A: imprint sync serve --relay host<br/>PIN + fingerprint shown on screen

    B->>R: WebSocket connect (consumer, room=abc123)
    Note over B: imprint sync host/abc123 --pin <PIN>

    B->>R: HELLO (hostname, user, os, fingerprint, pin)
    R->>A: forward HELLO
    Note over A: verify PIN (constant-time)<br/>auto-accept if fingerprint trusted<br/>else TTY prompt [y/n/t]
    A-->>R: status 200 / 403
    R-->>B: forward response

    B->>R: GET /sync/pull
    R->>A: forward request
    A-->>R: scroll Qdrant payload (JSON, no vectors)
    R-->>B: forward response
    Note over B: store_batch — re-embed locally, dedup by content hash

    B->>R: POST /sync/push (local-only records)
    R->>A: forward request
    Note over A: store_batch + dedup
    A-->>R: merge stats
    R-->>B: done

    Note over A,B: Both machines now have the same memories<br/>(re-embedded locally so model swap is safe)
```

The relay server is a stateless WebSocket forwarder. A public relay is hosted at `wss://imprint.alexandruleca.com` and used by default — pass `--relay` to point at your own. Room IDs expire after 1 hour. Vectors are **not** transferred over the wire — peers re-embed content locally, which means machines using different models or devices can sync without vector-format compatibility headaches.

### Choosing a relay

| Form | Example | Meaning |
|------|---------|---------|
| _(omit `--relay`)_ | `imprint sync serve` | Use default `wss://imprint.alexandruleca.com` |
| Bare host | `--relay sync.example.com` | Auto-pick scheme (`wss` unless localhost/127.*) |
| Explicit WSS | `--relay wss://sync.example.com` | Force `wss://` |
| Plain WS | `--relay ws://localhost:8430` | Force `ws://` (useful for local testing) |

The consumer accepts the same forms: a bare room ID uses the default relay; `<host>/<id>` or `wss://<host>/<id>` targets a specific relay.

### Authentication

Two factors gate every sync:

1. **PIN** — 8-char random alphanumeric (uppercase + lowercase + digits), freshly generated per `sync serve` session. Always required, even for previously trusted devices. Compared in constant time on the provider.
2. **Device fingerprint** — stable 8-char hex ID persisted at `data/device_id.txt`. Each machine has one. The provider prompts the user to accept an unknown fingerprint; trusted fingerprints (stored in `data/trusted_devices.json`) are auto-accepted after PIN check.

Prompt options on the provider side:
- `y` / `yes` — accept this session only
- `t` / `trust` — accept and remember the fingerprint
- anything else — reject

The relay sees neither the PIN nor the data — it only forwards opaque WebSocket frames between peers in a room.

```bash
# Use the default public relay
imprint sync serve
# → prints: imprint sync abc123 --pin Ab3xY9Kq
# → also prints: This device: hostname (id: a3f2c1d4)

imprint sync abc123 --pin Ab3xY9Kq          # Machine B, default relay

# Or self-host the relay
imprint relay --port 8430

imprint sync serve --relay sync.yourdomain.com
# → prints: imprint sync wss://sync.yourdomain.com/abc123 --pin Ab3xY9Kq

imprint sync wss://sync.yourdomain.com/abc123 --pin Ab3xY9Kq
# → Machine A sees hostname/user/os/fingerprint + prompts [y/n/t]
# → on accept: bidirectional merge, done
```

Prebuilt relay images are on GHCR — see [installation.md](./installation.md#run-the-relay-server-docker) for Docker run commands.

## Visualization

```bash
imprint viz
```

Opens an Obsidian-style force-directed graph in a Chrome app window:
- Sigma.js WebGL renderer — handles 100k+ nodes at interactive framerates
- ForceAtlas2 layout clusters same-project nodes together organically
- Hover highlights node + direct neighbors (Obsidian-style dim/bright)
- Click opens rich detail panel: tags, metadata, related nodes with similarity %, content preview
- Search highlights matching nodes, filter by project via legend
- Real-time updates via SSE when the imprint memory changes
- Pan, zoom, drag nodes to rearrange
