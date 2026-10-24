---
title: ADR 0033 — Stored secret settings
description: Persist the Hugging Face token as a masked secret setting in config.json, resolved per download with HF_TOKEN taking precedence.
---

# 0033 — Stored secret settings

- Status: Accepted
- Date: 2026-10-24
- Related: ADR 0004 (model materialization), ADR 0024 (local service boundaries)

## Context

A Windows user saw huggingface_hub warn about unauthenticated requests during ingestion and asked for a dashboard setting for a Hugging Face token. Imprint had no persisted credential of any kind. LLM provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) are read only from the environment. The settings schema (`imprint/config_schema.py`) is the single source for CLI, dashboard and runtime values, with env > `config.json` > default precedence. Every display surface echoed values verbatim, including `GET /api/config`, `imprint config`, `imprint status` and the dashboard edit field.

Hub downloads happen in several processes: the long-running dashboard API, the ingest/refresh/learn subprocesses it starts, MCP servers and hooks. Two code paths make them. `imprint/embeddings.py` calls `hf_hub_download`. `imprint/local_llm.py` streams the GGUF with httpx and sent no credentials.

## Decision

Add `huggingface.token` to the settings schema with the standard `HF_TOKEN` as its env var. A new `secret` attribute on `Setting` marks it. Storage and precedence are unchanged: an exported `HF_TOKEN` wins over the stored value, which wins over unset.

Secrets are masked at every display boundary through `config_schema.display_value`: CLI list/get/set/status output, `GET /api/config` and the `PUT` response. They show `****` plus the last four characters for values of 12 characters or more, and `****` alone otherwise. The API marks such settings `secret: true`. The dashboard edits them in an empty password field and never pre-fills them. `DELETE /api/config/{key}` gives the dashboard the CLI's `config reset` for any setting, which shows as **Remove** for a secret.

`config.hub_token()` resolves the token on each download, not at import. Both Hub paths pass it explicitly. `hf_hub_download(token=...)` gets it as an argument. `build_hf_headers(token=...)` supplies the GGUF request's headers, and httpx drops `Authorization` on redirects off huggingface.co. A token saved in the dashboard therefore reaches the running API server and every process it starts, without restarts or environment propagation. With no token configured, `None` keeps huggingface_hub's own resolution (`HF_TOKEN`, then `hf auth login`).

## Alternatives

- **Export the stored value into `os.environ["HF_TOKEN"]` at import.** This reaches third-party downloaders too, but resolution would then report the stored token as `env`. The long-running API server would keep a stale token after a dashboard change. Telling user-provided from exported values needs extra state.
- **Store it in huggingface_hub's token file** (`hf auth login`, or an Imprint `HF_TOKEN_PATH`). That is a second settings store outside `config.json`. The default location is shared machine-wide with the user's other tools, outlives uninstall and changes their identity.
- **A separate secrets file or OS keychain.** Stronger at rest, but it adds a storage backend and platform keychain dependencies for one token, where the data directory is already per-user. Revisit if Imprint stores more credentials.
- **Environment variable only.** The Electron app on Windows has no convenient place for users to set one, which is the reported problem.

## Consequences

- The token is stored in plaintext in `data/config.json`. `write_config` creates that file through `mkstemp`, so it is owner-only (`0600`) on POSIX. On Windows it inherits the per-user install directory's ACL. Complete uninstall removes it unless data is kept (ADR 0028). Backups of the data directory contain it.
- `imprint config set huggingface.token ...` exposes the token in shell history and process lists. The docs recommend the dashboard or `HF_TOKEN` instead.
- Chonkie's internal model2vec fallback downloads with huggingface_hub's implicit credentials only. It runs only when Imprint's own embedding model fails to load. It does not see the stored token.
- Future credentials can reuse `secret=True` and inherit the masking and dashboard behavior.

## Validation

- `tests/test_hub_token.py`:
  - stored token, `HF_TOKEN` precedence and reset
  - CLI masking and on-disk storage
  - short-value masking
  - the token passed to `hf_hub_download` and sent as a Bearer header for GGUF downloads
  - `GET`/`PUT`/`DELETE /api/config` through the ASGI app, with the raw token absent from responses
- The full Python suite passes (229 tests on Linux/WSL2, Python 3.12).
- `imprint/ui/tests/settings.spec.ts` checks the hidden empty input, masked display, removal and the environment-precedence note against the built static dashboard. Type-check and lint pass.
- Not verified: a real authenticated download against huggingface.co, and native Windows or macOS execution.
