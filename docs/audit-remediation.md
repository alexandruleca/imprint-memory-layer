---
title: Audit remediation tracker
description: Implemented audit corrections and evidence still required for release acceptance.
---

Reviewed 2026-10-23. This is the status of the source changes in this working tree. Completed implementation does not imply that all native environments or client versions have been executed.

| Area | Implemented | Remaining evidence/work |
| --- | --- | --- |
| Installers/releases | Correct setup completion/helper paths, architecture-specific staging, fresh dashboard, native pre-publication gates, complete checksums | Execute Windows/macOS native and interactive install/repair/upgrade/uninstall; sign/notarize with maintainer credentials. |
| Runtime dependencies | Python 3.12, hashed base/GPU/LLM locks, Electron 45, macOS 13 target, pinned uv/Qdrant archive hashes, release install/update verification | GPU/LLM repair paths still make unpinned requests; native wheel matrix; independently authenticated manifests and model revisions; nine high dashboard build-tool advisories. |
| Linux/container | Separate x64/ARM shell payloads, correct Qdrant ARM musl asset, container venv/PYTHONPATH/data paths, hash install and fresh UI | Native ARM runtime and real graphical desktop/AppImage checks; container ARM runtime. |
| Local boundary | Loopback Host and Origin checks, normal CLI bind restriction, explicit container namespace bind, sandboxed Electron and exact navigation | Authenticated remote mode, filesystem authorization, relay-focused review. |
| Client configuration | Recoverable backups, atomic writes, owned hooks only, preserved JSON fields/TOML nested settings, CODEX_HOME, current ChatGPT/Codex guidance, basic doctor | JSONC, concurrent edit protection, plan/diff/project scopes, all host/profile/version fixtures, live trust/connection tests. |
| Indexing | Complete identities, source hashes/path/size, safe replacement, verified empty/deleted cleanup, unified refresh, secret/symlink rules, explicit size/error policy, live dedup checks | Transactional generation switch, secret scanning, watcher, relocation/rename policy, cross-version sync migration. |
| Retrieval | Shared bounded hybrid fusion, exact terms, filters/provenance, semantic comparison, public ID lookup | Held-out corpus/recall and answer evaluation, BM25/reranker experiments, degraded lexical-only path. |
| MCP | Durable acknowledgement, deterministic tags and opt-outs, annotations, bounded reads/input, get_memory, captured workspace including URL writes | Broader protocol/resource/cancellation cases, all-host compatibility, durable long-running operation protocol. |
| Lifecycle/workspaces | Non-destructive Windows probes, shared lock inode, concurrent Qdrant startup lock, queue workspace capture, Go/Python data-dir agreement | PID reuse/process identity, Windows cancellation trees, crash stress tests, complete per-client binding and backup/restore. |
| UI | Usable search states/filters, async cancellation, modal focus, responsive navigation, visible reconnect, clean lint and browser gates | Broader screen-reader/contrast checks, onboarding/progress, all-page async consistency and native GUI testing. |
| Engineering process | Assessment, operational docs, roadmap, ADRs 0001–0008 and root working agreement | Normal review and native CI logs; benchmark/quality thresholds before stronger claims. |

Executed checks and their limits are in [platform validation](platform-validation.md). Product/logic details are in [UI review](ui-ux-review.md), [indexing and retrieval](indexing-retrieval.md), [integrations](integrations.md) and [quality roadmap](quality-roadmap.md).
