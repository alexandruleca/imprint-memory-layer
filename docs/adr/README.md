---
title: Architecture decision records
description: How Imprint records significant decisions and the current decision index.
---

## Process

Starting 2026-10-23, significant decisions are recorded here in the same change that implements them. Root `AGENTS.md` establishes this working agreement for future contributors and coding agents. Routine corrections can cite an existing ADR.

Use the next unused four-digit number and a descriptive filename. Copy [the template](template.md). Status is **Proposed**, **Accepted**, **Superseded** or **Rejected**. Include date, context, decision, alternatives, consequences and validation. A proposal is not authorization to claim the behavior exists. Human review follows the repository's normal pull-request process.

Preserve historical reasoning. If an accepted decision changes materially, create a new ADR linking to the old one, and mark the old record superseded with a link. Update this index, relevant operational documentation and validation evidence together. Do not invent historical decisions; label retrospective descriptions explicitly.

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-record-significant-decisions.md) | Accepted | Repository documentation and ADRs are the durable engineering record. |
| [0002](0002-build-and-validate-before-publication.md) | Accepted | Build all release artifacts and run native checks before publication. |
| [0003](0003-local-mcp-and-portable-hooks.md) | Accepted | Local stdio MCP, shared runtime preparation and portable hook commands. |
| [0004](0004-runtime-dependencies-and-local-boundaries.md) | Accepted | Resolve base dependencies and enforce explicit local browser/process boundaries. |
| [0005](0005-source-lifecycle-and-hybrid-retrieval.md) | Accepted | Content provenance, safe source replacement and bounded hybrid ranking. |
| [0006](0006-durable-mcp-and-preserved-client-config.md) | Accepted | Durable writes, workspace binding, diagnostics and preserved profiles. |
| [0007](0007-verified-runtime-and-desktop-baseline.md) | Accepted | Hashed dependencies/downloads, Electron 45, macOS 13 and container parity. |
| [0008](0008-dashboard-retrieval-and-accessibility.md) | Accepted | Same-origin dashboard, usable search states and accessible modal behavior. |
| [0009](0009-model-profiles-and-embedding-identity.md) | Proposed | Evaluate local model profiles with explicit embedding identity and safe re-embedding. |
| [0010](0010-qwen-defaults-and-explicit-reindex.md) | Accepted | Qwen defaults, collection encoding identity, explicit reindex and shared bounded helper. |
| [0011](0011-nightly-trivy-security-scanning.md) | Accepted | Nightly Trivy repository scans, retained reports and a high/critical failure gate. |
| [0012](0012-native-package-runtime-and-silent-installs.md) | Accepted | Platform-specific Intel ONNX runtime and bounded, noninteractive Windows installer smoke. |
| [0013](0013-remediate-security-scan-dependencies.md) | Accepted | Patched website/build dependencies, non-root relay and explicitly disabled LLM disk caching. |
| [0014](0014-main-only-release-packaging.md) | Accepted | Compile/test branches; package and publish release artifacts only from main. |
| [0015](0015-workspace-roots-and-source-purge.md) | Accepted | Approved workspace roots, confirmed sharing, stable writes and previewable file/directory purge. |
| [0016](0016-sync-direction-and-workspace-sharing.md) | Accepted (details superseded by 0018) | Per-machine sync direction policies, per-session workspace selection and copy migration for sharing curated workspaces. |
| [0017](0017-device-provenance-and-local-project-paths.md) | Accepted | Portable memory origin, current-device context and approved local project-path mappings. |
| [0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | Accepted | One Python sync implementation for CLI and dashboard; SPAKE2 + AES-256-GCM end-to-end encryption with no plaintext fallback. |
| [0019](0019-reproducible-benchmark-evidence.md) | Accepted | Frozen public corpora, paired trials, visible failures and auditable JSON statistics for benchmark claims. |
| [0020](0020-explicit-pairing-and-automatic-workspace-sync.md) | Accepted | Mutually approved workspace pairing, automatic encrypted merge, pause and acknowledged peer revocation. |
| [0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) | Accepted | Compact bounded MCP evidence, selective expansion and separately pinned runtime/corpus benchmark provenance. |
