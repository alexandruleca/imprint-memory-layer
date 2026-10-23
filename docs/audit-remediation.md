---
title: Audit remediation tracker
description: Implemented audit corrections and evidence still required for release acceptance.
---

Reviewed 2026-10-23. This is the status of the source changes in this working tree. Completed implementation does not imply that all native environments or client versions have been executed.

| Area | Implemented | Remaining evidence/work |
| --- | --- | --- |
| Installers/releases | Correct setup completion/helper paths, bounded silent Windows installs, Intel ONNX wheel policy, architecture-specific staging, native pre-publication gates, complete checksums; v0.8.1 passes all five native installer/runtime targets; main-only packaging | Interactive install/repair/upgrade/uninstall; sign/notarize with maintainer credentials. |
| Runtime dependencies | Python 3.12, hashed base/GPU/LLM locks, Electron 45, macOS 13 target, pinned uv/Qdrant archive hashes, native base wheels and release verification; npm audits zero advisories and Trivy zero high/critical | One unpatched medium diskcache finding remains visible with caching disabled. GPU/LLM repair/source-build reproducibility and independently authenticated manifests/model revisions remain open. |
| Linux/container | Separate x64/ARM shell payloads, native ARM runtime, correct Qdrant ARM musl asset, container venv/PYTHONPATH/data paths, hash install, fresh UI and bundled font | Real graphical desktop/AppImage checks; container ARM runtime. |
| Local boundary | Loopback Host and Origin checks, normal CLI bind restriction, explicit container namespace bind, sandboxed Electron and exact navigation | Authenticated remote mode, filesystem authorization, relay-focused review. |
| Client configuration | Recoverable backups, atomic writes, owned hooks only, preserved JSON fields/TOML nested settings, CODEX_HOME, current ChatGPT/Codex guidance, basic doctor | JSONC, concurrent edit protection, plan/diff/project scopes, all host/profile/version fixtures, live trust/connection tests. |
| Indexing | Complete identities, source hashes/path/size, safe replacement, verified empty/deleted cleanup, unified refresh, secret/symlink rules, explicit size/error policy, live dedup checks, device origin and approved local project mappings | Transactional generation switch, secret scanning, watcher, automatic relocation/rename policy, cross-version sync migration. |
| Retrieval | Shared bounded hybrid fusion, exact terms, filters/provenance, semantic comparison, public ID lookup | Held-out corpus/recall and answer evaluation, BM25/reranker experiments, degraded lexical-only path. |
| MCP | Durable acknowledgement, deterministic tags and opt-outs, annotations, bounded reads/input, get_memory, captured workspace including URL writes, device/cwd context and scoped source purge | Broader protocol/resource/cancellation cases, all-host compatibility, durable long-running operation protocol. |
| Lifecycle/workspaces | Non-destructive Windows probes, shared lock inode, concurrent Qdrant startup lock, queue workspace capture, Go/Python data-dir agreement, approved roots with confirmed overlap, previewed source purge/exclusions and late-write fences, portable shared knowledge with device provenance | PID reuse/process identity, Windows cancellation trees outside bounded installer setup, crash stress tests, complete per-client binding and backup/restore. |
| UI | Usable search states/filters, async cancellation, modal focus, responsive navigation, visible reconnect, clean lint and browser gates | Broader screen-reader/contrast checks, onboarding/progress, all-page async consistency and native GUI testing. |
| Engineering process | Assessment, operational docs, roadmap, ADRs for audit/models/security/release/workspace/device policy, root working agreement, nightly visible Trivy reports, native CI and installer logs | Normal review and ongoing evidence; benchmark/quality thresholds before stronger claims. |

Executed checks and their limits are in [platform validation](platform-validation.md). Product/logic details are in [UI review](ui-ux-review.md), [indexing and retrieval](indexing-retrieval.md), [integrations](integrations.md) and [quality roadmap](quality-roadmap.md).
