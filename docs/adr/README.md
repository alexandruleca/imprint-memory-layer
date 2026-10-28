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

- [ADR 0083](0083-apple-silicon-laptop-runner.md) **Apple silicon laptop runner.** Trusted ARM64 jobs use the laptop; PR checks remain hosted. Amended by [ADR 0106](0106-intel-mac-laptop-runner.md) (Intel jobs on the Intel laptop).
- [ADR 0106](0106-intel-mac-laptop-runner.md) **Intel Mac laptop runner.** Trusted Intel macOS jobs use the owner's Intel laptop and validate the package payload; no CI job installs a macOS package.

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

- [ADR 0081](0081-verifiable-redacted-source-snapshots.md) **Verifiable redacted source snapshots.** Complete redacted readable sources, character-span manifests and bounded verified source reads.

- [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) **Audio files enter the source ingestion pipeline.** Local audio becomes timestamped neutral-speaker conversations; existing redacted source snapshots provide the workspace-scoped transcript cache.

### Workspaces, ingestion and learning

- [ADR 0015](0015-workspace-roots-and-source-purge.md) **Workspace roots and source purge.** Approved workspace roots, confirmed sharing, stable writes and previewable file/directory purge. Amended by [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (learning rules) and [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (directory workspaces).
- [ADR 0029](0029-paired-browser-capture.md) **Paired local browser capture prototype.** Explicit browser capture through workspace-bound extension pairing and serialized local ingestion. Amended by [ADR 0030](0030-chrome-extension-release-zip.md) (Gmail availability and distribution).
- [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) **Learn from client stores by conversation ownership.** Learning reads the clients' own stores without an approved root; recorded working directories decide which workspace a conversation belongs to. Amended by [ADR 0037](0037-learn-every-integrated-client-store.md) (stores and Codex working directories), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) (Cursor folder names) and [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (project files). Amended by [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) (explicit directory-free inbox).
- [ADR 0037](0037-learn-every-integrated-client-store.md) **Learn from every integrated client store.** Learning reads this computer's Claude Code and Codex profiles and every registered WSL profile, each in the path context its hooks use. Amended by [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) (Cursor stores), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) (Cline stores) and [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) (Copilot stores).
- [ADR 0107](0107-git-lists-a-repositorys-files-wsl-repositories-through-their-distribution.md) **Git lists a repository's files; WSL repositories through their distribution.** Inside a git repository the scan indexes only what git lists and never enters ignored directories; a WSL repository is listed by its distribution's git, and a repository git cannot list fails the scan.
- [ADR 0040](0040-documents-with-nothing-to-index.md) **Documents with nothing to index.** A document without extractable text is skipped with a reason, not failed, and retried only when its bytes or the extraction profile change. Amended by [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) (default bundled OCR and retryable tool failures).
- [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) **Learn Cursor conversations from their folder names.** A Cursor folder name counts as a conversation's working directory when exactly one existing directory has it; Cursor's profile is a learn store and a WSL registration target. Amended by [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) (explicit directory-free inbox).
- [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md) **Learn Cline sessions from their manifests.** A Cline session's manifest is its working-directory evidence; Cline's sessions directory is a learn store, setup registers where Cline 4 reads, and `cline` is a WSL registration target.
- [ADR 0064](0064-single-slot-command-queue-and-shared-lock.md) **Serialize heavy commands with a single-slot queue and a shared lock file.** One heavy job at a time per data directory, enforced by an advisory lock on `data/queue.lock` that the Go CLI and the Python dashboard share; the dashboard queues its jobs in SQLite. Amended by [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) (background tagging and preemptible holders), [ADR 0068](0068-merge-identical-waiting-jobs.md) (merged submissions) [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) (the Stop hook's learner takes the lock) and [ADR 0089](0089-one-progress-record-for-the-running-job.md) (one progress record every surface reads).
- [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md) **Background LLM tagging that yields to every other job.** Storing paths mark memories instead of running the LLM tagger; a background job tags them newest first when nothing else waits, steps aside between memories and holds the lock as a holder any other taker may stop.
- [ADR 0068](0068-merge-identical-waiting-jobs.md) **Merge identical waiting jobs.** A submission identical to a waiting job (same command, workspace and normalized arguments) returns that job; one follow-up may wait behind an identical running job.
- [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) **Projects name their workspace and each device approves it.** A project's `.imprint/workspace.json`, honoured when this device's roots approve it, picks the workspace of sessions, hooks, `ingest`, `refresh` and learning there; sessions ask once and `use_workspace` assigns a project without a restart. Amended by [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) (explicit directory-free inbox).
- [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) **The Stop hook hands its turn to one waiting learner under the queue lock.** The Stop hook starts a detached learner and returns; at most one learner per conversation waits, lets a burst of turns settle, then learns in one process while holding the queue lock.
- [ADR 0089](0089-one-progress-record-for-the-running-job.md) **One progress record for the running job.** Every queue-lock holder, setup and the Stop hook's learner included, reports in `data/progress.json`; the dashboard shows it on every page, the desktop app while the server is down, and waiting commands print it.
- [ADR 0079](0079-learn-only-what-the-claude-code-user-typed.md) **Learn only what the Claude Code user typed.** Client context is not a human question. Amended by [ADR 0082](0082-retain-readable-conversation-evidence.md) (retain readable context as attributed evidence).
- [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md) **Conversations are learned again when their reading changes.** Learning replaces the current reading and reuses unchanged chunks. Amended by [ADR 0081](0081-verifiable-redacted-source-snapshots.md) (source profile and manifest checks).

- [ADR 0082](0082-retain-readable-conversation-evidence.md) **Retain readable conversation evidence.** Preserve available messages, analysis and tool evidence with explicit attribution and omission counts. Amended by [ADR 0088](0088-bytes-a-crash-never-wrote-are-counted-omissions.md) (unwritten bytes are counted omissions).
- [ADR 0088](0088-bytes-a-crash-never-wrote-are-counted-omissions.md) **Bytes a crash never wrote are counted omissions.** A reading drops the NUL bytes an interrupted write leaves and counts each run, instead of failing the transcript.

### Embeddings, local models and chat

- [ADR 0010](0010-qwen-defaults-and-explicit-reindex.md) **Qwen defaults and explicit reindex.** Qwen defaults, collection encoding identity, explicit reindex and shared bounded helper. Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (classification grammar) and [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) (default export and device).
- [ADR 0032](0032-one-onnx-runtime-per-environment.md) **One ONNX Runtime per environment.** Each profile installs base requirements plus exactly one ONNX Runtime build, verified from package records and repaired by setup. Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (runtime selection) and [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) (the CPU build only, for OCR and the privacy filter).
- [ADR 0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) **Prebuilt llama.cpp wheels and first-chat provisioning.** `llama-cpp-python` installs from upstream's hash-pinned prebuilt wheels where they exist; the first local chat installs the binding and downloads the model, with progress in the chat. Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) (GPU build) [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) (the binding in every installation) and [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) (macOS wheels built here).
- [ADR 0045](0045-setting-choices-and-gpu-auto-select.md) **Settings declare their choices; the GPU device defaults to auto.** Settings declare their choices; the GPU device defaults to auto. Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) (setup's device-0 check) and [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) (the GPU llama.cpp runs on).
- [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md) **llama.cpp GPU offload through upstream's Vulkan wheels.** The gpu and auto profiles install llama.cpp's hash-locked Vulkan build and keep it only when it finds a GPU; the nvcc rebuild is removed. Amended by [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) (one GPU per model).
- [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md) **Chat speaks each model's native tool protocol.** Chat keeps one registry of tool JSON schemas; local models get them through their chat template and remote providers through API function calling, with no text protocol of Imprint's own. Amended by [ADR 0061](0061-local-tool-call-formats.md) (local parsing). Amended by [ADR 0101](0101-chat-research-always-reserves-an-answer.md) (reserved final synthesis).
- [ADR 0061](0061-local-tool-call-formats.md) **Local tool-call formats by model family.** Local chat parses tool calls in the model family's format (Qwen XML, Hermes JSON, Llama 3, Mistral), detected by rendering a probe call through the template or chosen with `chat.tool_format`; other models chat without tools and say so.
- [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) **Embeddings run on llama.cpp, without CUDA.** Qwen's Q8_0 GGUF of Qwen3-Embedding-0.6B runs on llama.cpp: the Vulkan or Metal build on any GPU it finds, the CPU build elsewhere. The CUDA runtime and the int8 ONNX export are gone, and the binding is part of every installation. Amended by [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) (macOS installs prebuilt wheels).
- [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) **llama.cpp runs each model on one GPU.** Embeddings and the local LLM load whole onto `model.gpu_device`, by default the discrete GPU with the most memory, instead of llama.cpp's split across every GPU.
- [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) **macOS installs llama.cpp wheels built by this repository.** A manually run workflow builds the binding's macOS wheels, Metal on both architectures, from the hash-locked source and publishes them as the prerelease `llama-cpp-python-v<version>`; the locks pin them, so no Mac needs a compiler. Amended by [ADR 0106](0106-intel-mac-laptop-runner.md) (Intel wheel built on the laptop runner).
- [ADR 0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md) **Keep Qwen3.5-4B as the local tagger.** Qwen3.5-2B tags 2.2–2.5× faster and no worse at memory types, but worse at the language and topic tags search filters on; putting the prompt's fixed text first gains nothing on Qwen3.5's architecture.
- [ADR 0093](0093-sequential-chat-section-analysis.md) **Sequential chat section analysis.** Two configurable logical workers analyze oversized questions and evidence sequentially, with bounded notes and reply-local exact rereads.

### Sync and device provenance

- [ADR 0016](0016-sync-direction-and-workspace-sharing.md) **Sync direction and workspace sharing.** Per-machine sync direction policies, per-session workspace selection and copy migration for sharing curated workspaces. Amended by [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) (implementation details).
- [ADR 0017](0017-device-provenance-and-local-project-paths.md) **Device provenance and local project paths.** Portable memory origin, current-device context and approved local project-path mappings.
- [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) **Single sync implementation and end-to-end encryption.** One Python sync implementation for CLI and dashboard; SPAKE2 + AES-256-GCM end-to-end encryption with no plaintext fallback.
- [ADR 0020](0020-explicit-pairing-and-automatic-workspace-sync.md) **Explicit pairing and automatic workspace sync.** Mutually approved workspace pairing, automatic encrypted merge, pause and acknowledged peer revocation. Amended by [ADR 0073](0073-remove-a-pairing-from-one-device.md) (one-sided removal).
- [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) **Synced tags follow the latest retag.** Each memory carries a `tagged_at` stamp; sync gives a memory both devices hold the classification that was set last.
- [ADR 0073](0073-remove-a-pairing-from-one-device.md) **Remove a pairing from one device without the other's acknowledgement.** A confirmed removal deletes this device's pairing with a peer, its credentials and trust at once, without contacting the relay or the peer; forgetting on both devices stays the normal path.

### Security and privacy

- [ADR 0011](0011-nightly-trivy-security-scanning.md) **Nightly Trivy security scanning.** Nightly Trivy repository scans, retained reports and a high/critical failure gate.
- [ADR 0013](0013-remediate-security-scan-dependencies.md) **Remediate security scan dependencies.** Patched website/build dependencies, non-root relay and explicitly disabled LLM disk caching.
- [ADR 0024](0024-enforce-local-service-boundaries.md) **Enforce local service boundaries.** Loopback-only managed Qdrant, dashboard peer validation, legacy graph browser checks and explicit container isolation.
- [ADR 0033](0033-stored-secret-settings.md) **Stored secret settings.** Hugging Face token as a masked secret setting in config.json, resolved per download with `HF_TOKEN` taking precedence.
- [ADR 0060](0060-redact-secrets-before-memories-are-stored.md) **Redact secrets before memories are stored.** Every memory and fact write, and every tagger or summarizer prompt, replaces credentials with `[REDACTED:<kind>:<length>]` first; the most redacted version of a memory wins on every synced device; OpenAI Privacy Filter is opt-in.

### MCP clients and hooks

- [ADR 0003](0003-local-mcp-and-portable-hooks.md) **Local MCP and portable hooks.** Local stdio MCP, shared runtime preparation and portable hook commands.
- [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) **Durable MCP and preserved client configuration.** Durable writes, workspace binding, diagnostics and preserved profiles. Amended by [ADR 0015](0015-workspace-roots-and-source-purge.md) (workspace binding) and [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) (per-project sessions).
- [ADR 0026](0026-windows-installer-wsl-client-bridge.md) **Windows installer registration for WSL clients.** Opt-in WSL Claude Code/Codex registration through a shared Windows stdio runtime, path-aware hooks and preserved profiles. Amended by [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) (WSL cleanup on disable and uninstall) and [ADR 0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md) (the `imprint` command in WSL homes).
- [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) **Connect and disconnect clients without touching the runtime.** One client table drives every client command; `imprint connect` and `disconnect` only register and unregister clients, so the dashboard can run them, and Settings enrolls WSL profiles.
- [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) **The Copilot home is GitHub Copilot's store, configuration and hook root.** GitHub Copilot's surfaces share `COPILOT_HOME`: Imprint registers MCP, instructions and SessionStart/Stop hooks there, learns its session events by their recorded context, and `copilot` is a WSL registration target.
- [ADR 0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md) **WSL homes with a connected client get the imprint command.** `imprint wsl setup` puts the Windows bridge's CLI in the home's `~/.local/bin/imprint`, keeps any other `imprint` there (a Linux installation's link, for one), and removes it with the home's last registration.

### Dashboard and desktop

- [ADR 0008](0008-dashboard-retrieval-and-accessibility.md) **Dashboard retrieval and accessibility.** Same-origin dashboard, usable search states and accessible modal behavior. Amended by [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) (dialog primitive).
- [ADR 0025](0025-desktop-owned-background-service.md) **Desktop-owned background service.** Open the splash before Python startup, hide the dashboard independently and stop its owned API on explicit Quit. Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) (service registration).
- [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) **shadcn overlays and desktop drag handles.** Dashboard overlays and controls use the vendored shadcn (Base UI) components; the frameless window's drag handles stand down while an overlay is open.
- [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) **Registered dashboard server and supervised restart.** The serving dashboard registers itself; its launcher starts it again when it exits for a restart, so every update method replaces it and the dashboard reloads. Amended by [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) (launchers wait while setup changes the runtime; setup restarts the server).
- [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md) **The dashboard reads whole sources from their snapshots.** Topics group their memories by conversation or file, named by a conversation's first question; any source opens whole from its stored redacted snapshot, events read back from the snapshot's headers, with a topic's passages marked. A source without a snapshot is not rebuilt from chunks. Amended by [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) (audio events and filenames).

### Voice and Echo

- [ADR 0041](0041-personaplex-startup-memory-prototype.md) **PersonaPlex startup memory prototype.** Prepare a bounded read-only MCP memory snapshot and inject it into a pinned, isolated PersonaPlex runtime (experimental adapter).
- [ADR 0042](0042-lfm-audio-cpu-experiment.md) **Small LFM audio experiment on CPU.** Small, verified native LFM2.5-Audio Q4 inference on Linux x64 CPU with read-only MCP snapshots (experimental offline turns).
- [ADR 0043](0043-live-voice-and-echo-companion.md) **Local live voice and the Echo companion.** Private local speech worker, same-chat reasoning, opt-in VAD and a floating Echo companion (experimental live pilot). Amended by [ADR 0044](0044-native-desktop-voice-platforms.md) (native platforms), [ADR 0048](0048-anime-echo-and-desktop-recovery.md) (avatar asset), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) (microphone on Start) and [ADR 0092](0092-voice-generation-follows-playback.md) (playback pacing).
- [ADR 0044](0044-native-desktop-voice-platforms.md) **Native Windows and macOS voice.** Portable weights and native Windows/macOS/Linux speech workers, transparent companion and dashboard lifecycle control. Amended by [ADR 0046](0046-visible-echo-runtime-setup.md) (UI-driven setup), [ADR 0047](0047-bundled-private-voice-engine.md) (bundled engine), [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) (entitlements) and [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) (consent check).
- [ADR 0046](0046-visible-echo-runtime-setup.md) **Visible first-time Echo setup.** Explicit first-time Echo download with real progress, persistent dashboard status, prerequisite checks and owned setup cancellation. Amended by [ADR 0047](0047-bundled-private-voice-engine.md) (release toolchain policy).
- [ADR 0047](0047-bundled-private-voice-engine.md) **Bundle the private voice engine.** Native release engines remove the end-user compiler requirement while preserving explicit verified model downloads.
- [ADR 0048](0048-anime-echo-and-desktop-recovery.md) **Anime Echo and recoverable desktop controls.** Whole-head drag, a recovery shortcut and a floating launcher after Close/minimize for the Echo companion and dashboard. Amended by [ADR 0054](0054-echo-looks-declare-their-motion.md) (Echo's look) and [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) (drag handles and the launcher group).
- [ADR 0054](0054-echo-looks-declare-their-motion.md) **Echo's looks declare their motion and follow the pointer.** Looks declare gaze, spin and pulse as glTF node extras; the heads' eyes turn at their eye bones; Echo is a generated core that follows the pointer across the screen. Amended by [ADR 0097](0097-echo-looks-fire-like-synapses.md) (the flow extra and Echo's synaptic core).
- [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) **Echo opens the microphone only on the user's intent, after an OS consent check.** Enable microphone, or Start conversation once the user has chosen the microphone, asks the operating system for consent through scoped IPC before opening the device; the device remembers the choice, never the grant.
- [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) **The floating companion moves with the launcher and keeps its options in settings.** While the dashboard is hidden the companion docks beside the launcher and either window drags both; only the head and header drag it; everything except talking sits in a settings panel.
- [ADR 0071](0071-each-look-speaks-in-its-own-voice.md) **Each look speaks in its own voice.** The voice.avatar look also chooses Echo's voice, the native model's speaker on both reasoning routes plus a robotic effect for Echo; a session keeps the voice it started with.
- [ADR 0092](0092-voice-generation-follows-playback.md) **Voice generation follows playback.** Completed audio releases bounded playback credit; active replies use an inactivity timeout instead of a fixed whole-turn limit.
- [ADR 0097](0097-echo-looks-fire-like-synapses.md) **Echo's looks fire like synapses.** A flow node extra runs light through a node at moments stored on its vertices; Echo is a generated 3D neural network whose signals cascade from hubs, and each head gets a synaptic halo. Amended by [ADR 0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) (generated eye and shared morphs).
- [ADR 0098](0098-head-surfaces-take-light-and-generated-parts-glow.md) **Head surfaces take light; generated parts glow.** Lighting follows each glTF material: the heads' lit materials take a sky and a key light, unlit generated parts render as light that fades toward their silhouettes. Amended by [ADR 0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) (generated eye and shared morphs).

- [ADR 0096](0096-cpu-whisper-runtime-for-audio-imports.md) **CPU Whisper runtime for audio imports.** Locked native wheels provide CPU transcription with pinned multilingual models downloaded on first audio use.

### Runtime, installers and release

- [ADR 0002](0002-build-and-validate-before-publication.md) **Validate releases before publication.** Build all release artifacts and run native checks before publication. Amended by [ADR 0014](0014-main-only-release-packaging.md) (which branches build and publish releases).
- [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md) **Runtime dependencies and local boundaries.** Resolve base dependencies and enforce explicit local browser/process boundaries. Amended by [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) (deferred hash locks, Electron upgrade and Host defense).
- [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) **Verified runtime and desktop baseline.** Hashed dependencies/downloads, Electron 45, macOS 13 and container parity.
- [ADR 0012](0012-native-package-runtime-and-silent-installs.md) **Native package runtime and silent installs.** Platform-specific Intel ONNX runtime and bounded, noninteractive Windows installer smoke. Amended by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) (no LLM choice).
- [ADR 0014](0014-main-only-release-packaging.md) **Main-only release packaging.** Compile/test branches; package and publish release artifacts only from main. Amended by [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) (dev prereleases).
- [ADR 0023](0023-update-by-installation-type.md) **Update by installation type.** The dashboard updates through the CLI, which installs a release the way the installation was set up. Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) (dashboard server stop and restart).
- [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) **Symmetric uninstall and branch synchronization.** Disconnect managed clients before uninstall, retry failures and fast-forward dev after main changes. Amended by [ADR 0028](0028-complete-uninstall.md) (data retention), [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) (dev publishes prereleases) and [ADR 0085](0085-main-only-fast-forwards-to-dev.md) (no dev sync).
- [ADR 0028](0028-complete-uninstall.md) **Complete uninstall.** Uninstall stops every process of the installation and removes its runtime and data unless the user keeps the data. Amended by [ADR 0074](0074-uninstall-removes-the-shared-desktop-profile.md) (desktop profile).
- [ADR 0030](0030-chrome-extension-release-zip.md) **Chrome preview ZIP and release downloads.** Allowlisted Chrome preview ZIP in validated releases, website download/install guide and coming-soon integrations.
- [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md) **Ad-hoc signed macOS desktop bundle.** The macOS desktop app is signed ad hoc as a bundle so privacy consent can read its microphone usage text; packaging rejects an unsigned bundle. Amended by [ADR 0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) (enclosing installer app).
- [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) **Dev prereleases from the dev branch.** Releasable dev pushes publish `vX.Y.Z-dev.N` prereleases of the next stable version through the release pipeline, skipping commits main holds; `--dev` installs the highest release version. Amended by [ADR 0108](0108-installations-opt-in-to-dev-builds-in-settings.md) (installations opt in to dev builds).
- [ADR 0108](0108-installations-opt-in-to-dev-builds-in-settings.md) **Installations opt in to dev builds in Settings.** `update.channel` (stable or dev) chooses the releases offered; `imprint update` resolves it unless `--stable` or `--dev` is passed, so the dashboard and the terminal follow the same channel.
- [ADR 0074](0074-uninstall-removes-the-shared-desktop-profile.md) **Uninstall removes the shared desktop profile.** An uninstall that deletes data also deletes the desktop shell's Electron profile, which every installation on the account shares.

- [ADR 0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) **Microphone purpose in the macOS installer wrapper.** The enclosing `.pkg` app declares microphone usage as well as the nested Electron shell.

- [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) **Default OCR.** Install local models and the PDF renderer with the base runtime; keep OCR enabled unless explicitly disabled. Amended by [ADR 0109](0109-automatic-ocr-runtime-repair.md) (automatic missing-runtime repair).
- [ADR 0109](0109-automatic-ocr-runtime-repair.md) **Automatic OCR runtime repair.** Release the job lock, attempt noninteractive bootstrap once per managed command and retry in a fresh extraction worker when bundled OCR dependencies are missing.

- [ADR 0085](0085-main-only-fast-forwards-to-dev.md) **Main only fast-forwards to dev.** Every change reaches main by fast-forwarding main to a dev commit; pull requests target dev, and no workflow fast-forwards dev after pushes to main.

- [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) **Setup frees the runtime before replacing its files.** Setup holds the queue and runtime locks while it changes the venv; before it replaces or removes packages it restarts the dashboard server and, on Windows, stops every other process running from the runtime. Amended by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) (Settings starts no bootstrap).

- [ADR 0105](0105-project-analysis-continues-after-model-timeouts.md) **Project analysis continues after model timeouts.** Preserve identifier suggestions, warn and retry incomplete model analysis.
- [ADR 0099](0099-sources-can-belong-to-several-projects.md) **Sources can belong to several projects.** Reviewable local links add retrieval membership while preserving the canonical source. Amended by [ADR 0105](0105-project-analysis-continues-after-model-timeouts.md) (per-source timeout recovery).
- [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) **Explicit unassigned inbox.** One opted-in workspace may learn conversations without directory evidence; conflicting evidence still rejects them.

- [ADR 0101](0101-chat-research-always-reserves-an-answer.md) **Chat research always reserves an answer.** Tool rounds are bounded; exhaustion, repeating reads or a full research context transition to tools-free synthesis and preserved evidence. Amended by [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) (evidence novelty and resource budgets).

- [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) **Chat research follows progress and resource budgets.** No fixed round cutoff; bounded evidence-novelty tracking and reply-wide time/token reservations lead to synthesis.

- [ADR 0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) **Echo has a generated eye with shared morph controls.** An opaque generated iris follows gaze and the nucleus blinks with it through named morph targets.

- [ADR 0104](0104-bundle-uv-with-conditional-hash-lock-support.md) **Bundle uv with conditional hash-lock support.** Pin verified uv 0.13.6 so fresh native bootstrap resolves the platform-specific audio/ONNX requirements without weakening hashes.

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
| [0006](0006-durable-mcp-and-preserved-client-config.md) | 2026-10-23 | Amended by [ADR 0015](0015-workspace-roots-and-source-purge.md), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) | Durable MCP and preserved client configuration |
| [0007](0007-verified-runtime-and-desktop-baseline.md) | 2026-10-23 | Accepted | Verified runtime and desktop baseline |
| [0008](0008-dashboard-retrieval-and-accessibility.md) | 2026-10-23 | Amended by [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | Dashboard retrieval and accessibility |
| [0009](0009-model-profiles-and-embedding-identity.md) | 2026-10-23 | Proposed | Model profiles and embedding identity |
| [0010](0010-qwen-defaults-and-explicit-reindex.md) | 2026-10-23 | Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) | Qwen defaults and explicit reindex |
| [0011](0011-nightly-trivy-security-scanning.md) | 2026-10-23 | Accepted | Nightly Trivy security scanning |
| [0012](0012-native-package-runtime-and-silent-installs.md) | 2026-10-23 | Amended by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) | Native package runtime and silent installs |
| [0013](0013-remediate-security-scan-dependencies.md) | 2026-10-23 | Accepted | Remediate security scan dependencies |
| [0014](0014-main-only-release-packaging.md) | 2026-10-23 | Amended by [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md) | Main-only release packaging |
| [0015](0015-workspace-roots-and-source-purge.md) | 2026-10-23 | Amended by [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) | Workspace roots and source purge |
| [0016](0016-sync-direction-and-workspace-sharing.md) | 2026-10-23 | Amended by [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | Sync direction and workspace sharing |
| [0017](0017-device-provenance-and-local-project-paths.md) | 2026-10-23 | Accepted | Device provenance and local project paths |
| [0018](0018-single-sync-implementation-and-end-to-end-encryption.md) | 2026-10-23 | Accepted | Single sync implementation and end-to-end encryption |
| [0019](0019-reproducible-benchmark-evidence.md) | 2026-10-23 | Accepted | Reproducible benchmark evidence |
| [0020](0020-explicit-pairing-and-automatic-workspace-sync.md) | 2026-10-23 | Amended by [ADR 0073](0073-remove-a-pairing-from-one-device.md) | Explicit pairing and automatic workspace sync |
| [0021](0021-budgeted-mcp-evidence-and-selective-expansion.md) | 2026-10-23 | Accepted | Budgeted MCP evidence and selective expansion |
| [0022](0022-http-client-lifetime-during-model-work.md) | 2026-10-23 | Accepted | Retain HTTP clients during model work |
| [0023](0023-update-by-installation-type.md) | 2026-10-23 | Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) | Update by installation type |
| [0024](0024-enforce-local-service-boundaries.md) | 2026-10-23 | Accepted | Enforce local service boundaries |
| [0025](0025-desktop-owned-background-service.md) | 2026-10-23 | Amended by [ADR 0050](0050-registered-dashboard-server-and-supervised-restart.md) | Desktop-owned background service |
| [0026](0026-windows-installer-wsl-client-bridge.md) | 2026-10-23 | Amended by [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md), [ADR 0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md) | Windows installer registration for WSL clients |
| [0027](0027-symmetric-uninstall-and-branch-sync.md) | 2026-10-23 | Amended by [ADR 0028](0028-complete-uninstall.md), [ADR 0066](0066-dev-prereleases-from-the-dev-branch.md), [ADR 0085](0085-main-only-fast-forwards-to-dev.md) | Symmetric uninstall and branch synchronization |
| [0028](0028-complete-uninstall.md) | 2026-10-23 | Amended by [ADR 0074](0074-uninstall-removes-the-shared-desktop-profile.md) | Complete uninstall |
| [0029](0029-paired-browser-capture.md) | 2026-10-23 | Amended by [ADR 0030](0030-chrome-extension-release-zip.md) | Paired local browser capture prototype |
| [0030](0030-chrome-extension-release-zip.md) | 2026-10-23 | Accepted | Chrome preview ZIP and release downloads |
| [0031](0031-website-analytics-and-public-relay-status.md) | 2026-10-24 | Accepted | Website analytics events and public relay status |
| [0032](0032-one-onnx-runtime-per-environment.md) | 2026-10-24 | Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) | One ONNX Runtime per environment |
| [0033](0033-stored-secret-settings.md) | 2026-10-24 | Accepted | Stored secret settings |
| [0034](0034-learn-from-client-stores-by-conversation-ownership.md) | 2026-10-24 | Amended by [ADR 0037](0037-learn-every-integrated-client-store.md), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md), [ADR 0072](0072-projects-name-their-workspace-and-each-device-approves-it.md), [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) | Learn from client stores by conversation ownership |
| [0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) | 2026-10-24 | Amended by [ADR 0049](0049-llama-gpu-offload-through-vulkan-wheels.md), [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md), [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) | Prebuilt llama.cpp wheels and first-chat provisioning |
| [0036](0036-move-memories-between-projects.md) | 2026-10-24 | Accepted | Move memories between projects |
| [0037](0037-learn-every-integrated-client-store.md) | 2026-10-24 | Amended by [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md), [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) | Learn from every integrated client store |
| [0038](0038-gpu-runtime-ships-cuda-libraries.md) | 2026-10-24 | Superseded by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md); amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | GPU runtime ships its CUDA libraries |
| [0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | 2026-10-24 | Accepted | shadcn overlays and desktop drag handles |
| [0040](0040-documents-with-nothing-to-index.md) | 2026-10-24 | Amended by [ADR 0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) | Documents with nothing to index |
| [0041](0041-personaplex-startup-memory-prototype.md) | 2026-10-24 | Accepted | PersonaPlex startup memory prototype |
| [0042](0042-lfm-audio-cpu-experiment.md) | 2026-10-24 | Accepted | Small LFM audio experiment on CPU |
| [0043](0043-live-voice-and-echo-companion.md) | 2026-10-24 | Amended by [ADR 0044](0044-native-desktop-voice-platforms.md), [ADR 0048](0048-anime-echo-and-desktop-recovery.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md), [ADR 0092](0092-voice-generation-follows-playback.md) | Local live voice and the Echo companion |
| [0044](0044-native-desktop-voice-platforms.md) | 2026-10-24 | Amended by [ADR 0046](0046-visible-echo-runtime-setup.md), [ADR 0047](0047-bundled-private-voice-engine.md), [ADR 0055](0055-ad-hoc-signed-macos-desktop-bundle.md), [ADR 0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) | Native Windows and macOS voice |
| [0045](0045-setting-choices-and-gpu-auto-select.md) | 2026-10-24 | Amended by [ADR 0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md), [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) | Settings declare their choices; the GPU device defaults to auto |
| [0046](0046-visible-echo-runtime-setup.md) | 2026-10-24 | Amended by [ADR 0047](0047-bundled-private-voice-engine.md) | Visible first-time Echo setup |
| [0047](0047-bundled-private-voice-engine.md) | 2026-10-24 | Accepted | Bundle the private voice engine |
| [0048](0048-anime-echo-and-desktop-recovery.md) | 2026-10-24 | Amended by [ADR 0054](0054-echo-looks-declare-their-motion.md), [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) | Anime Echo and recoverable desktop controls |
| [0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | 2026-10-24 | Amended by [ADR 0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) | llama.cpp GPU offload through upstream's Vulkan wheels |
| [0050](0050-registered-dashboard-server-and-supervised-restart.md) | 2026-10-24 | Amended by [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) | Registered dashboard server and supervised restart |
| [0051](0051-synced-tags-follow-the-latest-retag.md) | 2026-10-24 | Accepted | Synced tags follow the latest retag |
| [0052](0052-learn-cursor-conversations-from-their-folder-names.md) | 2026-10-25 | Amended by [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) | Learn Cursor conversations from their folder names |
| [0053](0053-connect-clients-without-touching-the-runtime.md) | 2026-10-25 | Accepted | Connect and disconnect clients without touching the runtime |
| [0054](0054-echo-looks-declare-their-motion.md) | 2026-10-25 | Amended by [ADR 0097](0097-echo-looks-fire-like-synapses.md) | Echo's looks declare their motion and follow the pointer |
| [0055](0055-ad-hoc-signed-macos-desktop-bundle.md) | 2026-10-25 | Amended by [ADR 0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) | Ad-hoc signed macOS desktop bundle |
| [0056](0056-learn-cline-sessions-from-their-manifests.md) | 2026-10-25 | Accepted | Learn Cline sessions from their manifests |
| [0057](0057-copilot-home-as-store-config-and-hook-root.md) | 2026-10-25 | Accepted | The Copilot home is GitHub Copilot's store, configuration and hook root |
| [0058](0058-recall-batched-reads-with-optional-local-planning.md) | 2026-10-25 | Accepted | Recall: batched reads with optional local planning |
| [0059](0059-chat-speaks-each-models-native-tool-protocol.md) | 2026-10-25 | Amended by [ADR 0061](0061-local-tool-call-formats.md), [ADR 0101](0101-chat-research-always-reserves-an-answer.md) | Chat speaks each model's native tool protocol |
| [0060](0060-redact-secrets-before-memories-are-stored.md) | 2026-10-25 | Accepted | Redact secrets before memories are stored |
| [0061](0061-local-tool-call-formats.md) | 2026-10-25 | Accepted | Local tool-call formats by model family |
| [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | 2026-10-25 | Superseded by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md); amended by [ADR 0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) | CUDA only for GPU embedding on a supported NVIDIA GPU |
| [0063](0063-auto-spawned-local-qdrant-server.md) | 2026-04-13 | Accepted | Run Qdrant as an auto-spawned local server |
| [0064](0064-single-slot-command-queue-and-shared-lock.md) | 2026-04-19 | Amended by [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0068](0068-merge-identical-waiting-jobs.md), [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md), [ADR 0089](0089-one-progress-record-for-the-running-job.md) | Serialize heavy commands with a single-slot queue and a shared lock file |
| [0065](0065-qdrant-over-lancedb-as-the-vector-store.md) | 2026-04-13 | Accepted | Qdrant over LanceDB as the vector store |
| [0066](0066-dev-prereleases-from-the-dev-branch.md) | 2026-10-25 | Amended by [ADR 0108](0108-installations-opt-in-to-dev-builds-in-settings.md) | Dev prereleases from the dev branch |
| [0067](0067-background-llm-tagging-that-yields-to-every-job.md) | 2026-10-25 | Accepted | Background LLM tagging that yields to every other job |
| [0068](0068-merge-identical-waiting-jobs.md) | 2026-10-25 | Accepted | Merge identical waiting jobs |
| [0069](0069-echo-opens-the-microphone-only-on-the-users-intent.md) | 2026-10-25 | Accepted | Echo opens the microphone only on the user's intent, after an OS consent check |
| [0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) | 2026-10-25 | Accepted | The floating companion moves with the launcher and keeps its options in settings |
| [0071](0071-each-look-speaks-in-its-own-voice.md) | 2026-10-25 | Accepted | Each look speaks in its own voice |
| [0072](0072-projects-name-their-workspace-and-each-device-approves-it.md) | 2026-10-26 | Amended by [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) | Projects name their workspace and each device approves it |
| [0073](0073-remove-a-pairing-from-one-device.md) | 2026-10-26 | Accepted | Remove a pairing from one device without the other's acknowledgement |
| [0074](0074-uninstall-removes-the-shared-desktop-profile.md) | 2026-10-26 | Accepted | Uninstall removes the shared desktop profile |
| [0075](0075-microphone-purpose-in-the-macos-installer-wrapper.md) | 2026-10-26 | Accepted | Microphone purpose in the macOS installer wrapper |
| [0076](0076-default-ocr-with-wheel-installed-models-and-renderer.md) | 2026-10-26 | Amended by [ADR 0109](0109-automatic-ocr-runtime-repair.md) | Default OCR with wheel-installed models and renderer |
| [0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) | 2026-10-26 | Accepted | The Stop hook hands its turn to one waiting learner under the queue lock |
| [0078](0078-keep-qwen3-5-4b-as-the-local-tagger.md) | 2026-10-26 | Accepted | Keep Qwen3.5-4B as the local tagger |
| [0079](0079-learn-only-what-the-claude-code-user-typed.md) | 2026-10-26 | Amended by [ADR 0082](0082-retain-readable-conversation-evidence.md) | Learn only what the Claude Code user typed |
| [0080](0080-conversations-are-relearned-when-their-reading-changes.md) | 2026-10-26 | Amended by [ADR 0081](0081-verifiable-redacted-source-snapshots.md) | Conversations are learned again when their reading changes |
| [0081](0081-verifiable-redacted-source-snapshots.md) | 2026-10-26 | Accepted | Verifiable redacted source snapshots |
| [0082](0082-retain-readable-conversation-evidence.md) | 2026-10-26 | Amended by [ADR 0088](0088-bytes-a-crash-never-wrote-are-counted-omissions.md) | Retain readable conversation evidence |

| [0083](0083-apple-silicon-laptop-runner.md) | 2026-10-26 | Amended by [ADR 0106](0106-intel-mac-laptop-runner.md) | Apple silicon laptop runner |
| [0084](0084-setup-frees-the-runtime-before-replacing-its-files.md) | 2026-10-26 | Amended by [ADR 0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) | Setup frees the runtime before replacing its files |
| [0085](0085-main-only-fast-forwards-to-dev.md) | 2026-10-27 | Accepted | Main only fast-forwards to dev |
| [0086](0086-embeddings-run-on-llama-cpp-without-cuda.md) | 2026-10-27 | Amended by [ADR 0090](0090-macos-installs-llama-cpp-wheels-built-here.md) | Embeddings run on llama.cpp, without CUDA |
| [0087](0087-llama-cpp-runs-each-model-on-one-gpu.md) | 2026-10-27 | Accepted | llama.cpp runs each model on one GPU |
| [0088](0088-bytes-a-crash-never-wrote-are-counted-omissions.md) | 2026-10-27 | Accepted | Bytes a crash never wrote are counted omissions |
| [0089](0089-one-progress-record-for-the-running-job.md) | 2026-10-27 | Accepted | One progress record for the running job, read by every surface |
| [0090](0090-macos-installs-llama-cpp-wheels-built-here.md) | 2026-10-27 | Amended by [ADR 0106](0106-intel-mac-laptop-runner.md) | macOS installs llama.cpp wheels built by this repository |
| [0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md) | 2026-10-27 | Accepted | WSL homes with a connected client get the imprint command |
| [0092](0092-voice-generation-follows-playback.md) | 2026-10-28 | Accepted | Voice generation follows playback |
| [0093](0093-sequential-chat-section-analysis.md) | 2026-10-28 | Accepted | Sequential chat section analysis |
| [0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md) | 2026-10-28 | Amended by [ADR 0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) | The dashboard reads whole sources from their snapshots |
| [0095](0095-audio-files-enter-the-source-ingestion-pipeline.md) | 2026-10-28 | Accepted | Audio files enter the source ingestion pipeline |
| [0096](0096-cpu-whisper-runtime-for-audio-imports.md) | 2026-10-28 | Accepted | CPU Whisper runtime for audio imports |
| [0097](0097-echo-looks-fire-like-synapses.md) | 2026-10-28 | Amended by [ADR 0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) | Echo's looks fire like synapses |
| [0098](0098-head-surfaces-take-light-and-generated-parts-glow.md) | 2026-10-28 | Amended by [ADR 0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) | Head surfaces take light; generated parts glow |
| [0099](0099-sources-can-belong-to-several-projects.md) | 2026-10-28 | Amended by [ADR 0105](0105-project-analysis-continues-after-model-timeouts.md) | Sources can belong to several projects |
| [0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md) | 2026-10-28 | Accepted | Explicit workspace inbox for unassigned conversations |
| [0101](0101-chat-research-always-reserves-an-answer.md) | 2026-10-28 | Amended by [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) | Chat research always reserves an answer |
| [0102](0102-chat-research-follows-progress-and-resource-budgets.md) | 2026-10-28 | Accepted | Chat research follows progress and resource budgets |
| [0103](0103-echo-has-a-generated-eye-with-shared-morph-controls.md) | 2026-10-28 | Accepted | Echo has a generated eye with shared morph controls |
| [0104](0104-bundle-uv-with-conditional-hash-lock-support.md) | 2026-10-28 | Accepted | Bundle uv with conditional hash-lock support |
| [0105](0105-project-analysis-continues-after-model-timeouts.md) | 2026-10-28 | Accepted | Project analysis continues after model timeouts |
| [0106](0106-intel-mac-laptop-runner.md) | 2026-10-28 | Accepted | Intel Mac laptop runner |
| [0107](0107-git-lists-a-repositorys-files-wsl-repositories-through-their-distribution.md) | 2026-10-28 | Accepted | Git lists a repository's files; WSL repositories through their distribution |
| [0108](0108-installations-opt-in-to-dev-builds-in-settings.md) | 2026-10-28 | Accepted | Installations opt in to dev builds in Settings |
| [0109](0109-automatic-ocr-runtime-repair.md) | 2026-10-28 | Accepted | Automatic OCR runtime repair |

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
