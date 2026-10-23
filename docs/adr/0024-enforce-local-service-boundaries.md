---
title: ADR 0024 — Enforce local service boundaries
description: Loopback-only managed Qdrant, dashboard peer validation and explicit container network isolation.
---

- Status: Accepted
- Date: 2026-10-23
- Extends: [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md).

## Context

Default Qdrant and dashboard listeners already used loopback, but `qdrant.host` could cause the managed unauthenticated daemon to bind publicly. Inherited Qdrant configuration could enable cluster mode or browser CORS. Dashboard Host/Origin checks rejected unwanted browser requests but could not identify a remote client sending forged local headers after an accidental public ASGI bind. The older graph server still returned wildcard CORS and lacked browser boundary checks. The external Ollama provider's client URL did not constrain its server's bind address.

## Decision

Require a literal loopback IP or `localhost` for managed Qdrant, before even probing an existing endpoint and again before spawning. Bind `localhost` as `127.0.0.1`; enforce loopback for HTTP and gRPC, disable cluster mode and browser CORS in the spawned environment. Preserve explicitly selected BYO connections (`qdrant.no_spawn=true`) with operator-managed security. Do not restart or reconfigure unrelated running Qdrant processes.

Require literal loopback connection addresses for the native dashboard/API, including static pages and API documentation. Continue validating Host and any supplied Origin. Disable uvicorn proxy-header processing in the supported launcher so forwarded headers cannot substitute for the connecting address. Direct uvicorn invocations must also use `--no-proxy-headers`. Apply local peer/Host/Origin checks to the older graph server before dispatching any HTTP method and remove its wildcard CORS headers.

Keep `--container-bind --host 0.0.0.0` as an explicit exception to native peer checks: Docker NAT presents non-loopback addresses even for loopback host publications. The provided Compose file publishes only `127.0.0.1:8420:8420`. Container deployments require trusted internal networks and loopback host publication; forged Host/Origin headers are not remote authentication. Qdrant remains on container loopback. The optional sync relay intentionally accepts remote peers and remains separate from dashboard/database access.

Document that separately managed Ollama should bind `127.0.0.1:11434` through `OLLAMA_HOST`; Imprint does not silently change external inference servers during setup.

## Alternatives

Relying on CORS, Host headers or a firewall alone leaves accidental public listeners insufficiently defended. Silently normalizing every remote Qdrant host hides configuration mistakes. Removing container mode breaks the supported Docker dashboard. Requiring per-install credentials for all local services would require a separate authentication/bootstrap design; this decision establishes network isolation without claiming protection against local software.

## Consequences

Existing nonlocal managed-Qdrant configurations fail with an actionable error instead of opening a listener. Deliberate remote databases need BYO mode and independent security. Native clients continue using local HTTP without credentials. Existing independently managed listeners cannot be assumed safe just because a local endpoint responds. A public container port, an untrusted container network or a forwarding proxy can still defeat the container's network boundary. Remote dashboard access remains unsupported. Moving an independently managed Ollama service to loopback requires updating clients that used its LAN address; containers need a separate secured connectivity arrangement.

## Validation

Regression tests exercise forged Host/Origin/forwarding headers from remote peers, absent peers, IPv4/IPv6 loopback, rejected public launcher binds, the explicit container exception, legacy graph GET/POST/DELETE/preflight rejection before side effects, Qdrant rejection before probe/spawn, inherited environment overrides, BYO mode and IPv6 health URLs. Native Linux runtime/socket checks and the rebuilt container smoke passed; results are recorded in [platform validation](../platform-validation.md). Native Windows/macOS/ARM execution remains pending for these changes.
