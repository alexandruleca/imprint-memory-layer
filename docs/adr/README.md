---
title: Architecture decision records
description: How to read, write and maintain Imprint's architecture decision records, and the index of every decision.
---

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
| [0032](0032-one-onnx-runtime-per-environment.md) | Accepted (runtime selection superseded by [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)) | Each profile installs base requirements plus exactly one ONNX Runtime build, verified from package records and repaired by setup. |
| [0033](0033-stored-secret-settings.md) | Accepted | Hugging Face token as a masked secret setting in config.json, resolved per download with `HF_TOKEN` taking precedence. |
| [0034](0034-learn-from-client-stores-by-conversation-ownership.md) | Accepted | Learning reads the clients' own stores without an approved root; recorded working directories decide which workspace a conversation belongs to. |
| [0035](0035-prebuilt-llama-wheels-and-chat-provisioning.md) | Accepted | `llama-cpp-python` installs from upstream's hash-pinned prebuilt wheels where they exist; the first local chat installs the binding and downloads the model, with progress in the chat. |
| [0036](0036-move-memories-between-projects.md) | Accepted | Projects place memories their writers named; workspace rules applied at every write keep moves through refresh, learning and sync, without changing source keys. |
| [0037](0037-learn-every-integrated-client-store.md) | Accepted | Learning reads this computer's Claude Code and Codex profiles and every registered WSL profile, each in the path context its hooks use. |
| [0038](0038-gpu-runtime-ships-cuda-libraries.md) | Accepted (library set, gate and CUDA check superseded by [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)) | The GPU profile installs CUDA 13 and cuDNN from NVIDIA wheels in its hashed lock, gated on the driver's supported CUDA version. |
| [0039](0039-shadcn-overlays-and-desktop-drag-handles.md) | Accepted | Dashboard overlays and controls use the vendored shadcn (Base UI) components; the frameless window's drag handles stand down while an overlay is open. |
| [0040](0040-documents-with-nothing-to-index.md) | Accepted | A document without extractable text is skipped with a reason, not failed, and retried only when its bytes or the extraction profile change. |
| [0041](0041-personaplex-startup-memory-prototype.md) | Accepted (experimental adapter) | Prepare a bounded read-only MCP memory snapshot and inject it into a pinned, isolated PersonaPlex runtime; real speech/platform validation remains separate. |
| [0042](0042-lfm-audio-cpu-experiment.md) | Accepted (experimental offline turns) | Evaluate small, verified native LFM2.5-Audio Q4 inference on Linux x64 CPU with read-only MCP snapshots; live transport/platform support remains separate. |
| [0043](0043-live-voice-and-echo-companion.md) | Accepted (experimental live pilot) | Private local speech worker, same-chat reasoning, opt-in VAD and a floating Echo particle head. |
| [0044](0044-native-desktop-voice-platforms.md) | Accepted (native validation tracked separately) | Portable weights and native Windows/macOS/Linux speech workers, transparent companion and dashboard lifecycle control. |
| [0045](0045-setting-choices-and-gpu-auto-select.md) | Accepted (setup's check on device 0 superseded by [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md)) | Settings declare their choices; the GPU device defaults to the first one the CUDA runtime supports. |
| [0046](0046-visible-echo-runtime-setup.md) | Accepted | Explicit first-time Echo download with real progress, persistent dashboard status, prerequisite checks and owned setup cancellation. |
| [0047](0047-bundled-private-voice-engine.md) | Accepted | Native release engines remove the end-user compiler requirement while preserving explicit verified model downloads. |
| [0048](0048-anime-echo-and-desktop-recovery.md) | Accepted | Modeled anime hologram, whole-head drag, recovery shortcut and a floating launcher after Close/minimize. |
| [0049](0049-llama-gpu-offload-through-vulkan-wheels.md) | Accepted | The gpu and auto profiles install llama.cpp's hash-locked Vulkan build and keep it only when it finds a GPU; the nvcc rebuild is removed. |
| [0050](0050-registered-dashboard-server-and-supervised-restart.md) | Accepted | The serving dashboard registers itself; its launcher starts it again when it exits for a restart, so every update method replaces it and the dashboard reloads. |
| [0051](0051-synced-tags-follow-the-latest-retag.md) | Accepted | Each memory carries a tagged_at stamp; sync gives a memory both devices hold the classification that was set last. |
| [0052](0052-learn-cursor-conversations-from-their-folder-names.md) | Accepted | A Cursor folder name counts as a conversation's working directory when exactly one existing directory has it; Cursor's profile is a learn store and a WSL registration target. |
| [0053](0053-connect-clients-without-touching-the-runtime.md) | Accepted | One client table drives setup, enable, disable, `imprint wsl` and the new `imprint connect`, `disconnect` and `clients`; connect and disconnect only register and unregister clients, so the dashboard's Commands page can rehook and unhook them; Settings enrolls WSL profiles through `imprint wsl list --json` and the queue. |
| [0054](0054-echo-looks-declare-their-motion.md) | Accepted | Looks declare gaze, spin and pulse as glTF node extras; the heads' eyes turn at the source eye bones; Echo is a generated core that follows the pointer across the screen. |
| [0055](0055-ad-hoc-signed-macos-desktop-bundle.md) | Accepted | The macOS desktop app is signed ad hoc as a bundle so privacy consent can read its microphone usage text; packaging rejects an unsigned bundle. |
| [0056](0056-learn-cline-sessions-from-their-manifests.md) | Accepted | A Cline session's manifest is its working-directory evidence; Cline's sessions directory is a learn store, setup registers where Cline 4 reads (its data directory's MCP settings and global rules directory), and cline is a WSL registration target; no hook is wired. |
| [0057](0057-copilot-home-as-store-config-and-hook-root.md) | Accepted | GitHub Copilot's surfaces share COPILOT_HOME: Imprint registers MCP, always-on instructions and SessionStart/Stop hooks there instead of VS Code's mcp.json, learns its session events by their recorded context with whole-id keys, and copilot is a WSL registration target. |
| [0058](0058-recall-batched-reads-with-optional-local-planning.md) | Accepted | One additive `recall` MCP tool runs several read steps concurrently under one budget; without a plan it is `search`; an opt-in model on this computer (`recall.llm`) may add steps and answer with checked citations, falling back to the deterministic result on timeout or error. |
| [0059](0059-chat-speaks-each-models-native-tool-protocol.md) | Accepted | Chat keeps one registry of tool JSON schemas; the local model gets them through its own chat template and answers in its trained markup, remote providers use API function calling, and no text protocol of our own remains. |
| [0060](0060-redact-secrets-before-memories-are-stored.md) | Accepted | Every memory and fact write, and every tagger or summarizer prompt, replaces credentials with `[REDACTED:<kind>:<length>]` before IDs, embeddings or model calls, sync imports included. Each write keeps the most redacted version of a memory, so one `imprint scrub` reaches every synced device without storing any hash of a secret. Deterministic rules always run; OpenAI Privacy Filter is opt-in and fails writes rather than weaken them. |
| [0061](0061-local-tool-call-formats.md) | Accepted | Local chat parses tool calls in the format of the model's family (Qwen XML, Hermes JSON, Llama 3, Mistral), detected by rendering a probe call through the GGUF's template or chosen with chat.tool_format; a model with no format chats without tools and says so; generation decodes special tokens. |
| [0062](0062-cuda-only-for-gpu-embedding-on-a-supported-nvidia-gpu.md) | Accepted | The embedding runtime follows `model.device` and the machine: the CUDA build, with only the NVIDIA libraries its provider loads, is installed only for GPU embedding on an NVIDIA GPU this CUDA build supports; switching builds removes what the old one used, and Settings provisions through a queued bootstrap and a supervised restart. |
