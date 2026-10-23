---
title: ADR 0001 — Record significant decisions
description: Use repository documentation and ADRs for durable architectural reasoning.
---

- Status: Accepted
- Date: 2026-10-23
- Requested by: repository owner during repository analysis and packaging work

## Context

The repository already has subsystem documentation, but no indexed decision history or root contribution rule requiring it. Product, integration and installer behavior has diverged from some documentation. Chat history and assistant memories are insufficient as the only record available to future contributors.

## Decision

Keep engineering documentation in `docs/`, publish an index, maintain numbered ADRs for significant choices, and require updates through root `AGENTS.md` and `CONTRIBUTING.md`. Record context, alternatives, consequences and validation. Preserve prior rationale when superseding a record. Use existing ADRs for routine fixes.

## Alternatives

Chat-only decisions are difficult to discover and review. An external wiki splits source and operational knowledge. A full RFC process for every correction creates unnecessary overhead.

## Consequences

Architecture, supported platforms, storage/schema, integrations, dependency policy, security boundaries and releases now have a durable reviewable record. Authors must keep docs consistent. These files are also read by the Astro documentation site and therefore include front matter.

## Validation

Review the new index/template, contribution instructions and documentation site build. This record documents the policy beginning now; it does not assert that earlier choices were formally approved under it.
