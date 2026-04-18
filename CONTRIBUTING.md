# Contributing to Imprint

Thank you for your interest in the **Imprint Memory Layer**. This document explains how we work in this repository and what to expect when you open a pull request.

## Disclaimer

This guide is **informational** and **not** legal advice. It does **not** promise that any contribution will be reviewed, accepted, merged, released, or credited on a schedule. Processes described here **may change without notice**. Your use of the project and any contribution you make remain subject to the [LICENSE](LICENSE) and applicable law.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) first. We expect participants to follow it in this project’s community spaces.

## Ways to contribute

- **Bug reports** — reproducible steps and environment details save everyone time. Use the [Bug report](https://github.com/alexandruleca/imprint-memory-layer/issues/new/choose) issue form. **Security-sensitive bugs** — follow [SECURITY.md](SECURITY.md); do not use the public bug form.
- **Feature ideas** — describe the problem and proposed behavior. Use the [Feature request](https://github.com/alexandruleca/imprint-memory-layer/issues/new/choose) form, or start a [Discussion](https://github.com/alexandruleca/imprint-memory-layer/discussions) for open-ended design chat.
- **Documentation** — fixes and clarifications in `README.md` and `docs/` are always welcome.
- **Code** — follow the workflow below.

## Repository layout (high level)

- **Go** — CLI entrypoint, packaging, and wiring under the module root (`go.mod`). Most contributors touch `cmd/`, `internal/`, and the root `main` package.
- **Python** — MCP server, embeddings, chunking, Qdrant helpers under `imprint/`. The Go binary shells into this tree for heavy lifting.
- **Docs** — deep dives under [`docs/`](docs/); release and CI behavior in [`docs/building.md`](docs/building.md).

## Development setup

From a clone of the repo:

```bash
make build          # current OS/arch → build/imprint
go vet ./...
go test ./...
```

To exercise the full stack (Python venv, models, local Qdrant, MCP), use the installer or dev docs:

- [`docs/installation.md`](docs/installation.md) — channels, Docker, updater.
- [`docs/building.md`](docs/building.md) — cross-compiles, packaging, release workflows.

## Branches & pull requests

- **Default integration branch** is [`dev`](https://github.com/alexandruleca/imprint-memory-layer/tree/dev). Open PRs against `dev` unless a maintainer asks otherwise (for example an urgent hotfix agreed for `main`).
- **`main`** carries stable releases. The release workflow uses **Conventional Commits** on the commits that land on `main`. See [Release channels (CI)](docs/building.md#release-channels-ci) in `docs/building.md`.
- **Merge strategy note:** merging `dev` → `main` should preserve per-commit prefixes (`feat:`, `fix:`, …). A squash merge to `main` only works if the **squashed commit message / PR title** is itself conventional.

Fill in the [pull request template](.github/pull_request_template.md) when you open a PR — it mirrors what CI checks.

## What CI runs

On pull requests to `main` or `dev`, [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs:

```bash
go vet ./...
go test ./...
go build -ldflags "-s -w" -o /tmp/imprint .
```

Run the same commands locally before pushing. Workflows use a **self-hosted** runner; if CI fails only on the runner, paste the log snippet in the PR.

## Commits & releases

- Prefer **Conventional Commits** (`feat:`, `fix:`, `perf:`, `docs:`, `chore:`, …) so automated versioning on `main` can infer semver bumps. See [`docs/building.md`](docs/building.md#release-channels-ci) for which types trigger a release.
- Keep commits focused and reviewable. When in doubt, split refactors from behavior changes.

## Licensing

The project is licensed under the [Apache License 2.0](LICENSE). This section is a summary only; the **LICENSE** file controls. Contributions submitted as pull requests are normally understood to be offered under the same license in line with GitHub’s terms for user-supplied content—if you need an exception, coordinate with maintainers **before** opening the PR.

The default embedding model has **separate** upstream terms (Gemma); see the [License](README.md#license) section in the README. Do not commit model weights.

## Getting help

- [GitHub Issues](https://github.com/alexandruleca/imprint-memory-layer/issues)
- [GitHub Discussions](https://github.com/alexandruleca/imprint-memory-layer/discussions)
- Maintainer on X: [@AlexandruLeca](https://x.com/AlexandruLeca) (from the README [Contact](README.md#contact) section)

Thanks again for helping improve Imprint.
