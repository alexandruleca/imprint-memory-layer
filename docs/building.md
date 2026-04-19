---
title: Building & Releases
---

# Building & Releases

## Building from Source

```bash
make build        # current platform
make all          # cross-compile (linux, macOS, Windows)
```

`make all` writes all 5 platform binaries into `bin/imprint-{os}-{arch}{.exe}`.

Version is injected via ldflags — `make` runs `git describe --tags --always --dirty` and passes it into `main.version`. Override with `VERSION=vX.Y.Z make all`.

## Docker Relay

```bash
docker build -t imprint-relay .
docker run -p 8430:8430 imprint-relay
```

Or deploy to Docker Swarm with [`docker-compose.relay.yml`](../docker-compose.relay.yml). The Dockerfile accepts `--build-arg VERSION=vX.Y.Z` to stamp the release tag into the binary.

See [installation.md](./installation.md#run-the-relay-server-docker) for prebuilt GHCR images.

## Release Channels (CI)

Two GitHub Actions workflows drive releases:

| Workflow | Trigger | Output |
|---|---|---|
| [.github/workflows/dev-release.yml](../.github/workflows/dev-release.yml) | push to `dev` | `vX.Y.Z-dev.N` prerelease + GHCR `:dev` |
| [.github/workflows/release.yml](../.github/workflows/release.yml) | push to `main` (+ `workflow_dispatch` override) | `vX.Y.Z` stable release + GHCR `:latest`, `:vX.Y.Z`, `:vX.Y` |

The stable workflow runs a conventional-commit analyzer over `git log $LAST_STABLE..HEAD` and cuts a release only if bump-worthy commits are found:

- `feat:` / `feat(scope):` → minor
- `fix:` / `perf:` → patch
- `type!:` or `BREAKING CHANGE:` in body → major
- `chore:` / `docs:` / `ci:` / `style:` / `refactor:` → no release

Merge `dev` → `main` with a merge or rebase (not squash) so per-commit conventional prefixes survive. Squash only works if the PR title itself is conventional.

CI runs on PRs to `main` or `dev` ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) — `go vet`, `go test ./...`, `go build`.

All workflows run on a self-hosted runner (`runs-on: [self-hosted, linux, x64]`).
