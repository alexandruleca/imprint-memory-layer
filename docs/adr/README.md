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
| [0008](0008-dashboard-retrieval-and-accessibility.md) | Accepted (dialog primitive superseded by [0039](0039-shadcn-overlays-and-desktop-drag-handles.md)) | Same-origin dashboard, usable search states and accessible modal behavior. |
| [0009](0009-model-profiles-and-embedding-identity.md) | Proposed | Evaluate local model profiles with explicit embedding identity and safe re-embedding. |
| [0010](0010-qwen-defaults-and-explicit-reindex.md) | Accepted | Qwen defaults, collection encoding identity, explicit reindex and shared bounded helper. |
| [0011](0011-nightly-trivy-security-scanning.md) | Accepted | Nightly Trivy repository scans, retained reports and a high/critical failure gate. |
| [0012](0012-native-package-runtime-and-silent-installs.md) | Accepted | Platform-specific Intel ONNX runtime and bounded, noninteractive Windows installer smoke. |
| [0013](0013-remediate-security-scan-dependencies.md) | Accepted | Patched website/build dependencies, non-root relay and explicitly disabled LLM disk caching. |
| [0014](0014-main-only-release-packaging.md) | Accepted | Compile/test branches; package and publish release artifacts only from main. |
| [0015](0015-workspace-roots-and-source-purge.md) | Accepted; learning rules superseded by [0034](0034-learn-from-client-stores-by-conversation-ownership.md) | Approved workspace roots, confirmed sharing, stable writes and previewable file/directory purge. |
| [0016](0016-sync-direction-and-workspace-sharing.md) | Accepted (details superseded by 0018) | Per-machine sync direction policies, per-session workspace selection and copy migration for sharing curated workspaces. |
| [0017](0017-device-provenance-and-local-project-paths.md) | Accepted | Portable memory origin, current-device context and approved local project-path mappings. |
| [0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | Accepted | One Python sync implementation for CLI and dashboard; SPAKE2 + AES-256-GCM end-to-end encryption with no plaintext fallback. |
| [0019](0019-reproducible-benchmark-evidence.md) | Accepted | Frozen public corpora, paired trials, visible failures and auditable JSON statistics for benchmark claims. |
| [0020](0020-explicit-pairing-and-automatic-workspace-sync.md) | Accepted | Mutually approved workspace pairing, automatic encrypted merge, pause and acknowledged peer revocation. |
| [0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) | Accepted | Compact bounded MCP evidence, selective expansion and separately pinned runtime/corpus benchmark provenance. |
| [0022](0022-http-client-lifetime-during-model-work.md) | Accepted | Keep HTTP Qdrant clients alive through cold model work; retire the embedded-lock idle timer. |
| [0023](0023-update-by-installation-type.md) | Accepted; dashboard server stop/restart superseded by [0050](0050-registered-dashboard-server-and-supervised-restart.md) | The dashboard updates through the CLI, which installs a release the way the installation was set up. |
| [0024](0024-enforce-local-service-boundaries.md) | Accepted | Loopback-only managed Qdrant, dashboard peer validation, legacy graph browser checks and explicit container isolation. |
| [0025](0025-desktop-owned-background-service.md) | Accepted; unregistered desktop service superseded by [0050](0050-registered-dashboard-server-and-supervised-restart.md) | Open the splash before Python startup, hide the dashboard independently and stop its owned API on explicit Quit. |
| [0026](0026-windows-installer-wsl-client-bridge.md) | Accepted | Opt-in WSL Claude Code/Codex registration through a shared Windows stdio runtime, path-aware hooks and preserved profiles. |
| [0027](0027-symmetric-uninstall-and-branch-sync.md) | Accepted (data retention superseded by 0028) | Disconnect managed clients before uninstall, retry failures and fast-forward dev after main changes. |
| [0028](0028-complete-uninstall.md) | Accepted | Uninstall stops every process of the installation and removes its runtime and data unless the user keeps the data. |
| [0029](0029-paired-browser-capture.md) | Accepted (WIP branch) | Explicit browser capture through workspace-bound extension pairing and serialized local ingestion. |
| [0030](0030-chrome-extension-release-zip.md) | Accepted | Allowlisted Chrome preview ZIP in validated releases, website download/install guide and coming-soon integrations. |
| [0031](0031-website-analytics-and-public-relay-status.md) | Accepted | GA4 events for website actions, a live public-relay status from a CORS-readable `/health`, and opt-in analytics on the relay page. |
| [0032](0032-one-onnx-runtime-per-environment.md) | Accepted | Each profile installs base requirements plus exactly one ONNX Runtime build, verified from package records and repaired by setup. |
| [0033](0033-stored-secret-settings.md) | Accepted | Hugging Face token as a masked secret setting in config.json, resolved per download with `HF_TOKEN` taking precedence. |
| [0034](0034-learn-from-client-stores-by-conversation-ownership.md) | Accepted | Learning reads the clients' own stores without an approved root; recorded working directories decide which workspace a conversation belongs to. |
| [0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) | Accepted | `llama-cpp-python` installs from upstream's hash-pinned prebuilt wheels where they exist; the first local chat installs the binding and downloads the model, with progress in the chat. |
| [0036](0036-move-memories-between-projects.md) | Accepted | Projects place memories their writers named; workspace rules applied at every write keep moves through refresh, learning and sync, without changing source keys. |
| [0037](0037-learn-every-integrated-client-store.md) | Accepted | Learning reads this computer's Claude Code and Codex profiles and every registered WSL profile, each in the path context its hooks use. |
| [0038](0038-gpu-runtime-ships-cuda-libraries.md) | Accepted | The GPU profile installs CUDA 13 and cuDNN from NVIDIA wheels in its hashed lock, gated on the driver's supported CUDA version. |
| [0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | Accepted | Dashboard overlays and controls use the vendored shadcn (Base UI) components; the frameless window's drag handles stand down while an overlay is open. |
| [0040](0040-documents-with-nothing-to-index.md) | Accepted | A document without extractable text is skipped with a reason, not failed, and retried only when its bytes or the extraction profile change. |
| [0041](0041-personaplex-startup-memory-prototype.md) | Accepted (experimental adapter) | Prepare a bounded read-only MCP memory snapshot and inject it into a pinned, isolated PersonaPlex runtime; real speech/platform validation remains separate. |
| [0042](0042-lfm-audio-cpu-experiment.md) | Accepted (experimental offline turns) | Evaluate small, verified native LFM2.5-Audio Q4 inference on Linux x64 CPU with read-only MCP snapshots; live transport/platform support remains separate. |
| [0043](0043-live-voice-and-echo-companion.md) | Accepted (experimental live pilot) | Private local speech worker, same-chat reasoning, opt-in VAD and a floating Echo particle head. |
| [0044](0044-native-desktop-voice-platforms.md) | Accepted (native validation tracked separately) | Portable weights and native Windows/macOS/Linux speech workers, transparent companion and dashboard lifecycle control. |
| [0045](0045-setting-choices-and-gpu-auto-select.md) | Accepted | Settings declare their choices; the GPU device defaults to the first one the CUDA runtime supports. |
| [0046](0046-visible-echo-runtime-setup.md) | Accepted | Explicit first-time Echo download with real progress, persistent dashboard status, prerequisite checks and owned setup cancellation. |
| [0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | Accepted | The gpu and auto profiles install llama.cpp's hash-locked Vulkan build and keep it only when it finds a GPU; the nvcc rebuild is removed. |
| [0050](0050-registered-dashboard-server-and-supervised-restart.md) | Accepted | The serving dashboard registers itself; its launcher starts it again when it exits for a restart, so every update method replaces it and the dashboard reloads. |
