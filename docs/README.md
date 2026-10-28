---
title: Engineering documentation
description: How Imprint works, how to use, build and operate it, and the decisions and evidence behind it.
---

This directory is the engineering source of truth. The documentation website reads it through `site/src/content/docs`, which is a symlink to this directory. Keep YAML front matter on new Markdown pages so the site can build them. Repository-relative documentation links remain readable on GitHub; the site maps them to Starlight routes (including custom slugs) and repository source links at build time.

## How Imprint works

Start with the [architecture overview](architecture.md), then the subsystems: [indexing and retrieval](indexing-retrieval.md), [verifiable source memory](source-memory.md), [chunking](chunking.md), [tagging](tagging.md), [embeddings](embeddings.md), [workspaces](workspaces.md) (including [multi-project source links and the unassigned conversation inbox](workspaces.md#associate-one-source-with-several-projects)), the [queue](queue.md), [sync](sync.md), [devices and portable project paths](devices.md) and [secret redaction](redaction.md).

## Using it

[Installation](installation.md), the [dashboard](dashboard.md), [MCP tools](mcp.md), the [HTTP API](api.md), [client integrations](integrations.md), [configuration](configuration.md) and [local helper models](local-models.md). Audio files: [multilingual audio ingestion](audio-ingestion.md), with local Romanian/English transcription, timestamped source reading and queued imports. Voice: the [Echo voice companion](live-voice.md), a floating hologram with push-to-talk, optional hands-free listening, streamed local speech and the local chat model. Experimental browser capture: the [Chrome extension](browser-extension.md).

## Building and operating

[Building and releases](building.md), [Docker](docker.md) and [nightly security scanning](security-scanning.md).

## Evidence and measurement

[Platform validation](platform-validation.md) records what was executed, on which platform, and what remains unverified. The measurements behind retrieval and model choices are in the [benchmark assessment and methodology](benchmarks.md), [local model selection](local-model-selection.md) and [context budgets and cost experiments](context-budgets.md).

## Research and experiments

Explored designs, kept because decisions cite them. They describe what was tried, not current behavior: the [voice assistant and floating avatar feasibility](voice-avatar-feasibility.md) study, the [PersonaPlex memory prototype](personaplex-prototype.md) and the [LFM2.5-Audio local voice experiment](lfm-audio-prototype.md). The voice feature that shipped is the [Echo voice companion](live-voice.md).

## Decisions and upkeep

Use the [ADR index and process](adr/README.md) for significant decisions. Update the relevant operational docs and ADR index in the same pull request. An implemented decision is recorded as accepted; a proposal is not current behavior.

Validation results belong in the change description and, for release/platform milestones, in [platform validation](platform-validation.md). Never turn an unexecuted checklist into a passing result.
