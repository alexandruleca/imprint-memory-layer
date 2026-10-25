---
title: Architecture decision records
description: How to read, write and maintain Imprint's architecture decision records, and the index of every decision.
---

## How to read this

Each record captures one significant decision: the problem, what was chosen, what was rejected and why, the consequences, what would reopen it, and how it was validated. An accepted record is not rewritten; a later change is a new record that links back to it.

- **To learn how Imprint works now,** read [Current decisions](#current-decisions): only the records in force, grouped by area, each with its decision in one line.
- **Amended** records are in force, but later records changed part of them. Read a record together with the ones named after it; where they differ, the later record wins. **Superseded** records are history: follow *Superseded by* to the record in force.
- [Open proposals](#open-proposals) are under consideration and do not describe current behavior. The [decision log](#decision-log) lists every record in number order with its status.
- **To challenge a decision** in an issue or pull request, cite its number, for example ADR 0063. Check its *Rejected alternatives* first, then say which of its *Invalidation conditions* holds, or which one is missing.
- **To add or change a record,** follow [How to write and maintain ADRs](#how-to-write-and-maintain-adrs).

Records 0001 to 0062 were brought to this structure on 2026-10-25 (*Restructured* in their headers); their *Invalidation conditions* were derived then from each record's own text. A record marked *Retrospective* was written after its decision, from the code and history it cites.

## Current decisions

The records in force, by area. An amended record is listed with the records that changed it; read them together.

### Process and evidence

- [ADR 0001](0001-record-significant-decisions.md) **Record significant decisions.** Repository documentation and ADRs are the durable engineering record.
- [ADR 0019](0019-reproducible-benchmark-evidence.md) **Reproducible benchmark evidence.** Frozen public corpora, paired trials, visible failures and auditable JSON statistics for benchmark claims.
- [ADR 0031](0031-website-analytics-and-public-relay-status.md) **Website analytics events and public relay status.** GA4 events for website actions, a live public-relay status from a CORS-readable `/health`, and opt-in analytics on the relay page.

### Storage and retrieval

- [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md) **Source lifecycle and hybrid retrieval.** Content provenance, safe source replacement and bounded hybrid ranking.
- [ADR 0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) **Budgeted MCP evidence and selective expansion.** Compact bounded MCP evidence, selective expansion and separately pinned runtime/corpus benchmark provenance.
- [ADR 0022](0022-http-client-lifetime-during-model-work.md) **Retain HTTP clients during model work.** Keep HTTP Qdrant clients alive through cold model work; retire the embedded-lock idle timer.
- [ADR 0036](0036-move-memories-between-projects.md) **Move memories between projects.** Projects place memories their writers named; workspace rules applied at every write keep moves through refresh, learning and sync, without changing source keys.
- [ADR 0058](0058-recall-batched-reads-with-optional-local-planning.md) **Recall: batched reads with optional local planning.** One additive read-only `recall` tool runs several read steps concurrently under one budget; without a plan it is `search`; an opt-in local model may add steps and answer with checked citations.
- [ADR 0063](0063-auto-spawned-local-qdrant-server.md) **Run Qdrant as an auto-spawned local server.** Every Imprint process reaches one Qdrant server on loopback that the first process to need it starts; embedded mode admits one process at a time.
- [ADR 0065](0065-qdrant-over-lancedb-as-the-vector-store.md) **Qdrant over LanceDB as the vector store.** Memories live in Qdrant, reached through its server API so the auto-spawned local server and a separately run one serve the same code; it replaced LanceDB, which as the owner recalls could not run as a remote server.

### Workspaces, ingestion and learning

- [ADR 0015](0015-workspace-roots-and-source-purge.md) **Workspace roots and source purge.** Approved workspace roots, confirmed sharing, stable writes and previewable file/directory purge. Amended by [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (learning rules).
- [ADR 0029](0029-paired-browser-capture.md) **Paired local browser capture prototype.** Explicit browser capture through workspace-bound extension pairing and serialized local ingestion. Amended by [ADR 0030](0030-chrome-extension-release-zip.md) (Gmail availability and distribution).
- [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) **Learn from client stores by conversation ownership.** Learning reads the clients' own stores without an approved root; recorded working directories decide which workspace a conversation belongs to. Amended by [ADR 0037](0037-learn-every-integrated-client-store.md) (stores and Codex working directories) and [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) (Cursor folder names).
- [ADR 0037](0037-learn-every-integrated-client-store.md) **Learn from every integrated client store.** Learning reads this computer's Claude Code and Codex profiles and every registered WSL profile, each in the path context its hooks use. Amended by [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) (Cursor stores), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) (Cline stores) and [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) (Copilot stores).
- [ADR 0040](0040-documents-with-nothing-to-index.md) **Documents with nothing to index.** A document without extractable text is skipped with a reason, not failed, and retried only when its bytes or the extraction profile change.
- [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) **Learn Cursor conversations from their folder names.** A Cursor folder name counts as a conversation's working directory when exactly one existing directory has it; Cursor's profile is a learn store and a WSL registration target.
- [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) **Learn Cline sessions from their manifests.** A Cline session's manifest is its working-directory evidence; Cline's sessions directory is a learn store, setup registers where Cline 4 reads, and `cline` is a WSL registration target.
- [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) **Serialize heavy commands with a single-slot queue and a shared lock file.** One heavy job at a time per data directory, enforced by an advisory lock on `data/queue.lock` that the Go CLI and the Python dashboard share; the dashboard queues its jobs in SQLite. Amended by [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (background tagging and preemptible holders).
- [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) **Background LLM tagging that yields to every other job.** Storing paths mark memories instead of running the LLM tagger; a background job tags them newest first when nothing else waits, steps aside between memories and holds the lock as a holder any other taker may stop.

### Embeddings, local models and chat

- [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) **Qwen defaults and explicit reindex.** Qwen defaults, collection encoding identity, explicit reindex and shared bounded helper. Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (classification grammar).
- [ADR 0032](0032-one-onnx-runtime-per-environment.md) **One ONNX Runtime per environment.** Each profile installs base requirements plus exactly one ONNX Runtime build, verified from package records and repaired by setup. Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (runtime selection).
- [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) **Prebuilt llama.cpp wheels and first-chat provisioning.** `llama-cpp-python` installs from upstream's hash-pinned prebuilt wheels where they exist; the first local chat installs the binding and downloads the model, with progress in the chat. Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (GPU build).
- [ADR 0038](0038-gpu-runtime-ships-cuda-libraries.md) **GPU runtime ships its CUDA libraries.** The GPU profile installs CUDA 13 and cuDNN from NVIDIA wheels in its hashed lock, gated on the driver's supported CUDA version. Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (library set, driver gate and CUDA check).
- [ADR 0045](0045-setting-choices-and-gpu-auto-select.md) **Settings declare their choices; the GPU device defaults to auto.** Settings declare their choices; the GPU device defaults to the first one the CUDA runtime supports. Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (setup's device-0 check).
- [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) **llama.cpp GPU offload through upstream's Vulkan wheels.** The gpu and auto profiles install llama.cpp's hash-locked Vulkan build and keep it only when it finds a GPU; the nvcc rebuild is removed.
- [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md) **Chat speaks each model's native tool protocol.** Chat keeps one registry of tool JSON schemas; local models get them through their chat template and remote providers through API function calling, with no text protocol of Imprint's own. Amended by [ADR 0061](0061-local-tool-call-formats.md) (local parsing).
- [ADR 0061](0061-local-tool-call-formats.md) **Local tool-call formats by model family.** Local chat parses tool calls in the model family's format (Qwen XML, Hermes JSON, Llama 3, Mistral), detected by rendering a probe call through the template or chosen with `chat.tool_format`; other models chat without tools and say so.
- [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) **CUDA only for GPU embedding on a supported NVIDIA GPU.** The embedding runtime follows `model.device` and the machine: the CUDA build, with only the libraries its provider loads, installs only for GPU embedding on a supported NVIDIA GPU; switching builds removes what the old one used.

### Sync and device provenance

- [ADR 0016](0016-sync-direction-and-workspace-sharing.md) **Sync direction and workspace sharing.** Per-machine sync direction policies, per-session workspace selection and copy migration for sharing curated workspaces. Amended by [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (implementation details).
- [ADR 0017](0017-device-provenance-and-local-project-paths.md) **Device provenance and local project paths.** Portable memory origin, current-device context and approved local project-path mappings.
- [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) **Single sync implementation and end-to-end encryption.** One Python sync implementation for CLI and dashboard; SPAKE2 + AES-256-GCM end-to-end encryption with no plaintext fallback.
- [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) **Explicit pairing and automatic workspace sync.** Mutually approved workspace pairing, automatic encrypted merge, pause and acknowledged peer revocation.
- [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) **Synced tags follow the latest retag.** Each memory carries a `tagged_at` stamp; sync gives a memory both devices hold the classification that was set last.

### Security and privacy

- [ADR 0011](0011-nightly-trivy-security-scanning.md) **Nightly Trivy security scanning.** Nightly Trivy repository scans, retained reports and a high/critical failure gate.
- [ADR 0013](0013-remediate-security-scan-dependencies.md) **Remediate security scan dependencies.** Patched website/build dependencies, non-root relay and explicitly disabled LLM disk caching.
- [ADR 0024](0024-enforce-local-service-boundaries.md) **Enforce local service boundaries.** Loopback-only managed Qdrant, dashboard peer validation, legacy graph browser checks and explicit container isolation.
- [ADR 0033](0033-stored-secret-settings.md) **Stored secret settings.** Hugging Face token as a masked secret setting in config.json, resolved per download with `HF_TOKEN` taking precedence.
- [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) **Redact secrets before memories are stored.** Every memory and fact write, and every tagger or summarizer prompt, replaces credentials with `[REDACTED:<kind>:<length>]` first; the most redacted version of a memory wins on every synced device; OpenAI Privacy Filter is opt-in.

### MCP clients and hooks

- [ADR 0003](0003-local-mcp-and-portable-hooks.md) **Local MCP and portable hooks.** Local stdio MCP, shared runtime preparation and portable hook commands.
- [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) **Durable MCP and preserved client configuration.** Durable writes, workspace binding, diagnostics and preserved profiles. Amended by [ADR 0015](0015-workspace-roots-and-source-purge.md) (workspace binding).
- [ADR 0026](0026-windows-installer-wsl-client-bridge.md) **Windows installer registration for WSL clients.** Opt-in WSL Claude Code/Codex registration through a shared Windows stdio runtime, path-aware hooks and preserved profiles. Amended by [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) (WSL cleanup on disable and uninstall).
- [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) **Connect and disconnect clients without touching the runtime.** One client table drives every client command; `imprint connect` and `disconnect` only register and unregister clients, so the dashboard can run them, and Settings enrolls WSL profiles.
- [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) **The Copilot home is GitHub Copilot's store, configuration and hook root.** GitHub Copilot's surfaces share `COPILOT_HOME`: Imprint registers MCP, instructions and SessionStart/Stop hooks there, learns its session events by their recorded context, and `copilot` is a WSL registration target.

### Dashboard and desktop

- [ADR 0008](0008-dashboard-retrieval-and-accessibility.md) **Dashboard retrieval and accessibility.** Same-origin dashboard, usable search states and accessible modal behavior. Amended by [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) (dialog primitive).
- [ADR 0025](0025-desktop-owned-background-service.md) **Desktop-owned background service.** Open the splash before Python startup, hide the dashboard independently and stop its owned API on explicit Quit. Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) (service registration).
- [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) **shadcn overlays and desktop drag handles.** Dashboard overlays and controls use the vendored shadcn (Base UI) components; the frameless window's drag handles stand down while an overlay is open.
- [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) **Registered dashboard server and supervised restart.** The serving dashboard registers itself; its launcher starts it again when it exits for a restart, so every update method replaces it and the dashboard reloads.

### Voice and Echo

- [ADR 0041](0041-personaplex-startup-memory-prototype.md) **PersonaPlex startup memory prototype.** Prepare a bounded read-only MCP memory snapshot and inject it into a pinned, isolated PersonaPlex runtime (experimental adapter).
- [ADR 0042](0042-lfm-audio-cpu-experiment.md) **Small LFM audio experiment on CPU.** Small, verified native LFM2.5-Audio Q4 inference on Linux x64 CPU with read-only MCP snapshots (experimental offline turns).
- [ADR 0043](0043-live-voice-and-echo-companion.md) **Local live voice and the Echo companion.** Private local speech worker, same-chat reasoning, opt-in VAD and a floating Echo companion (experimental live pilot). Amended by [ADR 0044](0044-native-desktop-voice-platforms.md) (native platforms) and [ADR 0048](0048-anime-echo-and-desktop-recovery.md) (avatar asset).
- [ADR 0044](0044-native-desktop-voice-platforms.md) **Native Windows and macOS voice.** Portable weights and native Windows/macOS/Linux speech workers, transparent companion and dashboard lifecycle control. Amended by [ADR 0046](0046-visible-echo-runtime-setup.md) (UI-driven setup), [ADR 0047](0047-bundled-private-voice-engine.md) (bundled engine) and [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) (entitlements).
- [ADR 0046](0046-visible-echo-runtime-setup.md) **Visible first-time Echo setup.** Explicit first-time Echo download with real progress, persistent dashboard status, prerequisite checks and owned setup cancellation. Amended by [ADR 0047](0047-bundled-private-voice-engine.md) (release toolchain policy).
- [ADR 0047](0047-bundled-private-voice-engine.md) **Bundle the private voice engine.** Native release engines remove the end-user compiler requirement while preserving explicit verified model downloads.
- [ADR 0048](0048-anime-echo-and-desktop-recovery.md) **Anime Echo and recoverable desktop controls.** Whole-head drag, a recovery shortcut and a floating launcher after Close/minimize for the Echo companion and dashboard. Amended by [ADR 0054](0054-echo-looks-declare-their-motion.md) (Echo's look).
- [ADR 0054](0054-echo-looks-declare-their-motion.md) **Echo's looks declare their motion and follow the pointer.** Looks declare gaze, spin and pulse as glTF node extras; the heads' eyes turn at their eye bones; Echo is a generated core that follows the pointer across the screen.

### Runtime, installers and release

- [ADR 0002](0002-build-and-validate-before-publication.md) **Validate releases before publication.** Build all release artifacts and run native checks before publication. Amended by [ADR 0014](0014-main-only-release-packaging.md) (which branches build and publish releases).
- [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md) **Runtime dependencies and local boundaries.** Resolve base dependencies and enforce explicit local browser/process boundaries. Amended by [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (deferred hash locks, Electron upgrade and Host defense).
- [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) **Verified runtime and desktop baseline.** Hashed dependencies/downloads, Electron 45, macOS 13 and container parity.
- [ADR 0012](0012-native-package-runtime-and-silent-installs.md) **Native package runtime and silent installs.** Platform-specific Intel ONNX runtime and bounded, noninteractive Windows installer smoke.
- [ADR 0014](0014-main-only-release-packaging.md) **Main-only release packaging.** Compile/test branches; package and publish release artifacts only from main. Amended by [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) (dev prereleases).
- [ADR 0023](0023-update-by-installation-type.md) **Update by installation type.** The dashboard updates through the CLI, which installs a release the way the installation was set up. Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) (dashboard server stop and restart).
- [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) **Symmetric uninstall and branch synchronization.** Disconnect managed clients before uninstall, retry failures and fast-forward dev after main changes. Amended by [ADR 0028](0028-complete-uninstall.md) (data retention) and [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) (dev publishes prereleases).
- [ADR 0028](0028-complete-uninstall.md) **Complete uninstall.** Uninstall stops every process of the installation and removes its runtime and data unless the user keeps the data.
- [ADR 0030](0030-chrome-extension-release-zip.md) **Chrome preview ZIP and release downloads.** Allowlisted Chrome preview ZIP in validated releases, website download/install guide and coming-soon integrations.
- [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) **Ad-hoc signed macOS desktop bundle.** The macOS desktop app is signed ad hoc as a bundle so privacy consent can read its microphone usage text; packaging rejects an unsigned bundle.
- [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) **Dev prereleases from the dev branch.** Releasable dev pushes publish `vX.Y.Z-dev.N` prereleases of the next stable version through the release pipeline, skipping commits main holds; `--dev` installs the highest release version.

## Open proposals

Under consideration; not current behavior.

- [ADR 0009](0009-model-profiles-and-embedding-identity.md) **Model profiles and embedding identity.** Evaluate local model profiles with explicit embedding identity and safe re-embedding.

## Decision log

Every record in number order. *Date* is when the decision was made.

| ADR | Date | Status | Title |
| --- | --- | --- | --- |
| [0001](0001-record-significant-decisions.md) | 2026-10-23 | Accepted | Record significant decisions |
| [0002](0002-build-and-validate-before-publication.md) | 2026-10-23 | Amended by [ADR 0014](0014-main-only-release-packaging.md) | Validate releases before publication |
| [0003](0003-local-mcp-and-portable-hooks.md) | 2026-10-23 | Accepted | Local MCP and portable hooks |
| [0004](0004-runtime-dependencies-and-local-boundaries.md) | 2026-10-23 | Amended by [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) | Runtime dependencies and local boundaries |
| [0005](0005-source-lifecycle-and-hybrid-retrieval.md) | 2026-10-23 | Accepted | Source lifecycle and hybrid retrieval |
| [0006](0006-durable-mcp-and-preserved-client-config.md) | 2026-10-23 | Amended by [ADR 0015](0015-workspace-roots-and-source-purge.md) | Durable MCP and preserved client configuration |
| [0007](0007-verified-runtime-and-desktop-baseline.md) | 2026-10-23 | Accepted | Verified runtime and desktop baseline |
| [0008](0008-dashboard-retrieval-and-accessibility.md) | 2026-10-23 | Amended by [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | Dashboard retrieval and accessibility |
| [0009](0009-model-profiles-and-embedding-identity.md) | 2026-10-23 | Proposed | Model profiles and embedding identity |
| [0010](0010-qwen-defaults-and-explicit-reindex.md) | 2026-10-23 | Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | Qwen defaults and explicit reindex |
| [0011](0011-nightly-trivy-security-scanning.md) | 2026-10-23 | Accepted | Nightly Trivy security scanning |
| [0012](0012-native-package-runtime-and-silent-installs.md) | 2026-10-23 | Accepted | Native package runtime and silent installs |
| [0013](0013-remediate-security-scan-dependencies.md) | 2026-10-23 | Accepted | Remediate security scan dependencies |
| [0014](0014-main-only-release-packaging.md) | 2026-10-23 | Amended by [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) | Main-only release packaging |
| [0015](0015-workspace-roots-and-source-purge.md) | 2026-10-23 | Amended by [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) | Workspace roots and source purge |
| [0016](0016-sync-direction-and-workspace-sharing.md) | 2026-10-23 | Amended by [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | Sync direction and workspace sharing |
| [0017](0017-device-provenance-and-local-project-paths.md) | 2026-10-23 | Accepted | Device provenance and local project paths |
| [0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | 2026-10-23 | Accepted | Single sync implementation and end-to-end encryption |
| [0019](0019-reproducible-benchmark-evidence.md) | 2026-10-23 | Accepted | Reproducible benchmark evidence |
| [0020](0020-explicit-pairing-and-automatic-workspace-sync.md) | 2026-10-23 | Accepted | Explicit pairing and automatic workspace sync |
| [0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) | 2026-10-23 | Accepted | Budgeted MCP evidence and selective expansion |
| [0022](0022-http-client-lifetime-during-model-work.md) | 2026-10-23 | Accepted | Retain HTTP clients during model work |
| [0023](0023-update-by-installation-type.md) | 2026-10-23 | Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) | Update by installation type |
| [0024](0024-enforce-local-service-boundaries.md) | 2026-10-23 | Accepted | Enforce local service boundaries |
| [0025](0025-desktop-owned-background-service.md) | 2026-10-23 | Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) | Desktop-owned background service |
| [0026](0026-windows-installer-wsl-client-bridge.md) | 2026-10-23 | Amended by [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) | Windows installer registration for WSL clients |
| [0027](0027-symmetric-uninstall-and-branch-sync.md) | 2026-10-23 | Amended by [ADR 0028](0028-complete-uninstall.md), [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) | Symmetric uninstall and branch synchronization |
| [0028](0028-complete-uninstall.md) | 2026-10-23 | Accepted | Complete uninstall |
| [0029](0029-paired-browser-capture.md) | 2026-10-23 | Amended by [ADR 0030](0030-chrome-extension-release-zip.md) | Paired local browser capture prototype |
| [0030](0030-chrome-extension-release-zip.md) | 2026-10-23 | Accepted | Chrome preview ZIP and release downloads |
| [0031](0031-website-analytics-and-public-relay-status.md) | 2026-10-24 | Accepted | Website analytics events and public relay status |
| [0032](0032-one-onnx-runtime-per-environment.md) | 2026-10-24 | Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | One ONNX Runtime per environment |
| [0033](0033-stored-secret-settings.md) | 2026-10-24 | Accepted | Stored secret settings |
| [0034](0034-learn-from-client-stores-by-conversation-ownership.md) | 2026-10-24 | Amended by [ADR 0037](0037-learn-every-integrated-client-store.md), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) | Learn from client stores by conversation ownership |
| [0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) | 2026-10-24 | Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | Prebuilt llama.cpp wheels and first-chat provisioning |
| [0036](0036-move-memories-between-projects.md) | 2026-10-24 | Accepted | Move memories between projects |
| [0037](0037-learn-every-integrated-client-store.md) | 2026-10-24 | Amended by [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md), [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) | Learn from every integrated client store |
| [0038](0038-gpu-runtime-ships-cuda-libraries.md) | 2026-10-24 | Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | GPU runtime ships its CUDA libraries |
| [0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | 2026-10-24 | Accepted | shadcn overlays and desktop drag handles |
| [0040](0040-documents-with-nothing-to-index.md) | 2026-10-24 | Accepted | Documents with nothing to index |
| [0041](0041-personaplex-startup-memory-prototype.md) | 2026-10-24 | Accepted | PersonaPlex startup memory prototype |
| [0042](0042-lfm-audio-cpu-experiment.md) | 2026-10-24 | Accepted | Small LFM audio experiment on CPU |
| [0043](0043-live-voice-and-echo-companion.md) | 2026-10-24 | Amended by [ADR 0044](0044-native-desktop-voice-platforms.md), [ADR 0048](0048-anime-echo-and-desktop-recovery.md) | Local live voice and the Echo companion |
| [0044](0044-native-desktop-voice-platforms.md) | 2026-10-24 | Amended by [ADR 0046](0046-visible-echo-runtime-setup.md), [ADR 0047](0047-bundled-private-voice-engine.md), [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) | Native Windows and macOS voice |
| [0045](0045-setting-choices-and-gpu-auto-select.md) | 2026-10-24 | Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | Settings declare their choices; the GPU device defaults to auto |
| [0046](0046-visible-echo-runtime-setup.md) | 2026-10-24 | Amended by [ADR 0047](0047-bundled-private-voice-engine.md) | Visible first-time Echo setup |
| [0047](0047-bundled-private-voice-engine.md) | 2026-10-24 | Accepted | Bundle the private voice engine |
| [0048](0048-anime-echo-and-desktop-recovery.md) | 2026-10-24 | Amended by [ADR 0054](0054-echo-looks-declare-their-motion.md) | Anime Echo and recoverable desktop controls |
| [0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | 2026-10-24 | Accepted | llama.cpp GPU offload through upstream's Vulkan wheels |
| [0050](0050-registered-dashboard-server-and-supervised-restart.md) | 2026-10-24 | Accepted | Registered dashboard server and supervised restart |
| [0051](0051-synced-tags-follow-the-latest-retag.md) | 2026-10-24 | Accepted | Synced tags follow the latest retag |
| [0052](0052-learn-cursor-conversations-from-their-folder-names.md) | 2026-10-25 | Accepted | Learn Cursor conversations from their folder names |
| [0053](0053-connect-clients-without-touching-the-runtime.md) | 2026-10-25 | Accepted | Connect and disconnect clients without touching the runtime |
| [0054](0054-echo-looks-declare-their-motion.md) | 2026-10-25 | Accepted | Echo's looks declare their motion and follow the pointer |
| [0055](0055-ad-hoc-signed-macos-desktop-bundle.md) | 2026-10-25 | Accepted | Ad-hoc signed macOS desktop bundle |
| [0056](0056-learn-cline-sessions-from-their-manifests.md) | 2026-10-25 | Accepted | Learn Cline sessions from their manifests |
| [0057](0057-copilot-home-as-store-config-and-hook-root.md) | 2026-10-25 | Accepted | The Copilot home is GitHub Copilot's store, configuration and hook root |
| [0058](0058-recall-batched-reads-with-optional-local-planning.md) | 2026-10-25 | Accepted | Recall: batched reads with optional local planning |
| [0059](0059-chat-speaks-each-models-native-tool-protocol.md) | 2026-10-25 | Amended by [ADR 0061](0061-local-tool-call-formats.md) | Chat speaks each model's native tool protocol |
| [0060](0060-redact-secrets-before-memories-are-stored.md) | 2026-10-25 | Accepted | Redact secrets before memories are stored |
| [0061](0061-local-tool-call-formats.md) | 2026-10-25 | Accepted | Local tool-call formats by model family |
| [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | 2026-10-25 | Accepted | CUDA only for GPU embedding on a supported NVIDIA GPU |
| [0063](0063-auto-spawned-local-qdrant-server.md) | 2026-04-13 | Accepted | Run Qdrant as an auto-spawned local server |
| [0064](0064-single-slot-command-queue-and-shared-lock.md) | 2026-04-19 | Amended by [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) | Serialize heavy commands with a single-slot queue and a shared lock file |
| [0065](0065-qdrant-over-lancedb-as-the-vector-store.md) | 2026-04-13 | Accepted | Qdrant over LanceDB as the vector store |
| [0066](0066-dev-prereleases-from-the-dev-branch.md) | 2026-10-25 | Accepted | Dev prereleases from the dev branch |
| [0067](0067-background-llm-tagging-that-yields-to-every-job.md) | 2026-10-25 | Accepted | Background LLM tagging that yields to every other job |

## How to write and maintain ADRs

Records are useful only while they stay consistent: a reader must be able to trust that every record has the same shape, that its status is current, and that a decision which changed points to what replaced it. These rules apply to every record from 0001 on; `tests/test_adr_records.py` enforces the mechanical parts.

### When to write one

Starting 2026-10-23, significant decisions are recorded here in the same change that implements them; root `AGENTS.md` makes this the working agreement for contributors and coding agents. Write a record when a change alters storage or schema, runtime or dependency policy, client configuration or hooks, security boundaries, supported platforms, installer or update behavior, or release architecture. A routine fix cites the existing record instead of adding one. A proposal is not authorization to claim the behavior exists. Human review follows the repository's normal pull-request process.

**One decision per record.** A record answers one question, so it can be cited, challenged and superseded on its own. When a change makes several independent decisions, write several records. Some early records bundle related choices; they stay as they are.

### Files and numbering

- Copy [the template](template.md) to `docs/adr/NNNN-short-title.md`, using the next unused four-digit number.
- The front matter needs `title: ADR NNNN — Title` and a one-sentence `description`. The website parses it as YAML: quote a title that contains a colon. The body has no `#` heading; the site renders the title.
- Never renumber or delete a published record. Superseded and rejected records stay, so links and citations keep working.
- Two branches can take the same number. Whichever merges second renumbers its record before it merges: the filename, the title and every link to it.

### Header

The header is the bullet list right after the front matter, with these fields in this order:

| Field | When | Meaning |
| --- | --- | --- |
| Status | always | One word from the vocabulary below. |
| Date | always | When the decision was made, `YYYY-MM-DD`. Never changed afterwards. |
| Recorded | retrospective records | When the record was written. |
| Retrospective | retrospective records | `reconstructed from …`, naming the code, docs and commits used. |
| Scope | when the acceptance is limited | For example an experimental adapter, a branch-only implementation, or validation recorded separately. |
| Supersedes / Superseded by | full replacement | Links to the other record. |
| Amends / Amended by | partial change | Links to the other record, each followed by ` — ` and the part it changes. |
| Related | optional | Other records and docs in the same area that this record does not change. |
| Restructured | records normalized after acceptance | The date of the normalization. Sections added then (for the 2026-10-25 restructuring: *Invalidation conditions*) were derived from the record's own text, not written by its original authors. |

Link records as `[ADR NNNN](NNNN-short-title.md)`. When a *Supersedes*, *Superseded by*, *Amends* or *Amended by* field names several records, each goes on its own indented bullet under the field.

### Status vocabulary

| Status | Meaning |
| --- | --- |
| **Proposed** | Under consideration. Nothing is implemented on its authority, and documentation must not describe it as current behavior. |
| **Accepted** | In force as written. |
| **Amended** | In force, but one or more later records change part of it. Its *Amended by* field names them and what each changes. Read them together; where they differ, the later record wins. |
| **Superseded** | No longer in force. The record in *Superseded by* replaces it entirely. |
| **Rejected** | Considered and not adopted. It stays so the same proposal is not reopened without new evidence. |

Qualifiers do not go on the Status line: limits of an acceptance go in *Scope*, and changes by later records in *Amended by* or *Superseded by*.

### Sections

Every record has these sections, in this order. Others, such as *Security review*, may sit between them where they fit.

1. **Context.** The concrete problem or constraint, with its evidence: code, logs, measurements, the request.
2. **Decision.** What is chosen and how far it reaches.
3. **Rejected alternatives.** Each option that was actually considered, tried or measured, and why it was discarded, with the evidence where it exists. This section is what stops a settled debate from resurfacing, so it lists only options that really were weighed; it is never padded with options nobody evaluated. When none were recorded, it says so: *No alternative evaluation was recorded.*
4. **Consequences.** Benefits, costs, compatibility effects, migrations and known gaps.
5. **Invalidation conditions.** Concrete, observable conditions that would repeal the decision or reopen it: an upstream capability appearing or disappearing, a measurement crossing a stated line, a constraint from the context going away, a requirement the decision excluded becoming necessary. Write each one as the condition and its effect, for example *If Qdrant's embedded client supports several processes reading and writing one storage path, retire the auto-spawned daemon in favor of direct disk access.* "If requirements change" is not a condition. Required for every record in force (Accepted or Amended); a proposal states them before it is accepted.
6. **Validation.** What was executed, on which platforms, and what remains unverified. Never present an unexecuted check as passing.

### Changing a decision

An accepted record's reasoning is never rewritten. When a decision changes materially, write a new record:

- **It replaces the old decision entirely:** the new record says *Supersedes*, and the old record's status becomes **Superseded** with *Superseded by*.
- **It changes part of the old decision:** the new record says *Amends* and which part; the old record's status becomes **Amended** with *Amended by* and the same part.

Both links, the old record's status and this index change in the same pull request. On the old record, only header metadata changes; do not append follow-up paragraphs to its body (notes earlier records already carry are kept as history). When an invalidation condition is met, the response is a new record that supersedes or amends the old one, not an edit to the old one's conclusions. Typos and broken links may be fixed in place.

### Retrospective records

A decision made before anyone recorded it can be recorded later, for example when its rationale lived only inside another document:

- *Date* is when the decision was made, as far as the history shows; *Recorded* is today; *Retrospective* names the evidence.
- Use only what the code, documentation, commits and tests show, and cite it. Never invent tests, measurements, rejected alternatives or motives. Where the history is silent, say so (*No alternative evaluation was recorded.*), and list the open question in the pull request instead of answering it.
- *Invalidation conditions* look forward, so they may be written at recording time, derived from the recorded context.

### Keeping the rest of the documentation consistent

- Architecture and operational docs (`docs/architecture.md`, subsystem pages, `CLAUDE.md`, `AGENTS.md`) summarize a decision in a sentence or two and link to its record. The rationale — why, the rejected alternatives, the trade-offs — lives only in the record.
- This index changes with every new record and every status change: the record goes in the decision log, and, while it is in force, under its area in the current decisions.
- `tests/test_adr_records.py` checks that every record is indexed with its current status and title, that numbers are contiguous and match filenames and titles, that front matter, header fields and statuses follow these rules, that supersede and amend links resolve and are reciprocated, that the required sections are present in order, and that links between records resolve.
