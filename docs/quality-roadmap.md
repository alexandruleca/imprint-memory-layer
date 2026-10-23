---
title: Quality roadmap
description: Prioritized engineering work with concrete acceptance criteria and release gates.
---

This backlog is a set of recommendations from the 2026-10-23 review, not a commitment that every item is implemented. Accepted decisions are in the [ADR index](adr/README.md).

Implemented corrections are tracked in [audit remediation](audit-remediation.md). The remaining backlog is:

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| P0 | Interactive release acceptance | Automated native installer/runtime gates pass on Windows, both macOS architectures and Linux x64/ARM in v0.8.1. Retain those gates and verify graphical launch, interactive install/upgrade/repair/uninstall with existing memories; see [platform validation](platform-validation.md). |
| P0 | Sign/notarize and establish publisher provenance | Windows signatures, macOS notarization/stapling, owned certificates and an independently authenticated manifest. Existing SHA256 verification is necessary but does not authenticate a compromised release publisher. |
| P1 | Dependency advisory upkeep | Website advisories and the dashboard's vulnerable glob chain are remediated in ADR 0013. One unfixed medium diskcache advisory remains visible in the optional LLM profile; disk caching is explicitly disabled. Keep nightly findings and dependency changes under review. |
| P1 | Native optional-profile reproducibility | Verify GPU/LLM wheel availability and repair/source-build requests against approved versions/hashes on supported targets. Base/GPU/LLM lock files are implemented. |
| P1 | Embedding profiles and model migration | Qwen encoding, collection/cache/sync identity and forced reindex are implemented in ADR 0010. Comparative quality/resource evaluation, automatic generation promotion/rollback and cross-process helper sharing remain open; see [model analysis](local-model-selection.md) and proposed ADR 0009. |
| P1 | Retrieval evaluation | Held-out exact/paraphrase/missing/stale/cross-project fixtures; recall@k, MRR, answer correctness, attribution and p50/p95. Compare semantic and hybrid before weight tuning. |
| P1 | State integrity and lifecycle | Crash recovery, PID reuse, Windows cancellation trees, native lock interoperability and workspace stress. Basic startup locking and captured queue workspaces are implemented. |
| P1 | Backup/restore and migrations | Versioned Qdrant/SQLite/config/queue snapshot, clean-machine restore and cross-version ID/sync fixtures. |
| P1 | Integration management | Extend doctor beyond primary Codex profile, add plan/diff and scope selection, JSONC and concurrent-edit protection, real client trust/connection fixtures. Backups/preservation and basic handshake doctor are implemented. |
| P1 | Onboarding/freshness/accessibility | Time to first result, visible source freshness/failure and model phases, screen-reader graph alternative, native GUI/keyboard walkthrough. Search/modal/responsive/reconnect improvements are implemented. |
| P2 | Stronger ingestion lifecycle | Generation-based atomic replacement, comprehensive secret rules, watcher/debounce, relocation/rename handling and large-corpus limits. Hash provenance and safe retry are implemented. |
| P2 | Remote service boundary | Explicit authenticated remote mode and filesystem authorization; keep local Host/Origin protections and container loopback publication. |
| P2 | Durable MCP launcher/client packages | Relocation-safe registrations, no downloads during initialize, then client-native packages or a small IDE extension. |
| P2 | True offline distribution | Native Python, wheelhouse, Qdrant, approved models, licenses and signed manifest; install/search with networking disabled. |
| P2 | Maintainability and protocols | Extract lifecycle/retrieval/API stages behind contracts; shared Go/Python sync fixtures, negotiation and hostile-input tests. |

## Checks to retain

- Go vet/tests on all five native targets; portable hook and process-liveness regression tests.
- Dashboard lint, production build, Playwright interaction tests and Electron archive contents.
- Base Python lock installation and dependency checks, MCP discovery, HTTP/static health and isolated Qdrant storage smoke.
- Native package installation and bootstrap before release publication, plus checksums covering installers as well as archives.
- Review an ADR and docs diff when a change affects the architectural areas in root `AGENTS.md`.

## Measurement

Track clean-install success by platform/profile, time to first successful search, MCP startup/search p95, peak RAM during ingest, cancellation completion, index freshness, retrieval recall and answer accuracy. Re-run the benchmark with held-out prompts, repeated trials, model/client versions and error cases. A lower token count with an incorrect or stale answer is a regression.
