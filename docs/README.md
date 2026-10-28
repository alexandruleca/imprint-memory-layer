---
title: Engineering documentation
description: Repository review, integration strategy, operational documentation and architecture decisions.
---

This directory is the engineering source of truth. The documentation website reads it through `site/src/content/docs`, which is a symlink to this directory. Keep YAML front matter on new Markdown pages so the site can build them. Repository-relative documentation links remain readable on GitHub; the site maps them to Starlight routes (including custom slugs) and repository source links at build time.

Start with the [repository assessment](repository-assessment.md), [integration strategy](integrations.md), [quality roadmap](quality-roadmap.md) and [platform validation](platform-validation.md). The assessment is dated 2026-10-23 and separates source findings, changes implemented, and work still requiring evidence.

Detailed follow-up: [audit remediation tracker](audit-remediation.md), [indexing and retrieval](indexing-retrieval.md), [UI/UX review](ui-ux-review.md), and [local model selection](local-model-selection.md) and [local helper setup](local-models.md).

Agent efficiency: [context budgets and cost experiments](context-budgets.md).

Audio files: [multilingual audio ingestion](audio-ingestion.md), with local
Romanian/English transcription, timestamped source reading and queued imports.

Project organization: [reviewable multi-project source links and the unassigned
conversation inbox](workspaces.md#associate-one-source-with-several-projects).

Proposed voice interface: [voice assistant and floating avatar feasibility](voice-avatar-feasibility.md), including NVIDIA component choices, local chat routing, interruption handling and phased validation.

Experimental implementation: [PersonaPlex memory prototype](personaplex-prototype.md), with read-only MCP startup snapshots and a separate pinned voice runtime.

Primary smaller-model experiment: [LFM2.5-Audio local voice](lfm-audio-prototype.md), with verified native CPU artifacts, read-only memory snapshots and offline speech tests.

Live implementation: [Echo voice companion](live-voice.md), with a modeled anime hologram, floating window, push-to-talk, optional hands-free VAD, streamed local speech and routing to the existing local chat model.

Cross-device context: [devices and portable project paths](devices.md).

Experimental browser capture: [Chrome extension (WIP)](browser-extension.md).

Measurement: [benchmark assessment and methodology](benchmarks.md), and [verifiable source memory and its paired coverage experiment](source-memory.md).

Ongoing checks: [nightly security scanning](security-scanning.md).

Existing subsystem references: [architecture](architecture.md), [configuration](configuration.md), [MCP](mcp.md), [HTTP API](api.md), [building and releases](building.md), [installation](installation.md), [workspaces](workspaces.md), [queue](queue.md), [sync](sync.md), [secret redaction](redaction.md), [embeddings](embeddings.md), [chunking](chunking.md), [tagging](tagging.md) and [Docker](docker.md).

## Decisions and upkeep

Use the [ADR index and process](adr/README.md) for significant decisions. Update the relevant operational docs and ADR index in the same pull request. An implemented decision is recorded as accepted; an idea in the roadmap is not automatically an accepted commitment.

Validation results belong in the change description and, for release/platform milestones, in [platform validation](platform-validation.md). Never turn an unexecuted checklist into a passing result.
