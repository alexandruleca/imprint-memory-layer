---
title: ADR 0006 — Durable MCP and preserved client configuration
description: Honest write acknowledgements, bounded reads, workspace binding and non-destructive adapter edits.
---

- Status: Accepted
- Date: 2026-10-23
- Follow-up: Workspace binding is extended by [ADR 0015](0015-workspace-roots-and-source-purge.md); the original rationale below is retained.

## Context

Daemon-thread MCP stores could acknowledge work that disappears at process exit. Forced LLM tagging ignored the user's opt-out and could introduce remote calls. Client configuration rewrites could discard options, nested tables or user hook handlers. Machine-global workspace switching could redirect a queued job before execution.

## Decision

MCP `store` completes embedding/persistence before returning its ID. It uses deterministic local tags and preserves explicit domain tags; optional expensive retagging is separate. LLM provider selection does not enable LLM tagging. Zero-shot opt-out also applies to batch fallback. Expose read/write/destructive/open-world tool annotations, search/store bounds, bounded inline input and a read-only `get_memory` tool. Limit file reads to 20 chunks per call and a 12,000-character content budget with continuation guidance. Tool annotations describe intent; they are not authorization or a sandbox.

Capture/validate workspace at each MCP call boundary and key first-search context by workspace. URL ingest passes that workspace to storage. Queue submission records its workspace and the dispatcher passes a validated `IMPRINT_WORKSPACE` override to its subprocess. Directory/single-file indexing pins its workspace for the operation. Old queued jobs without the captured field retain historical behavior. This is not yet persistent per-project/client workspace binding.

Back up modified JSON/TOML profiles and atomically replace files using unique temporary paths. Merge Imprint server options and environment rather than discard unknown options. Replace only Imprint-owned hook handlers, preserving other handlers in matching groups. Validate TOML before and after rewriting owned sections; keep unrelated sections/comments. Normalize formatting inside edited owned stanzas as needed. Malformed or unsupported shapes fail without writing. There is no JSONC parser or cross-process compare-and-swap yet.

Provide read-only `imprint doctor [--json] [--probe-mcp]`: runtime/dependency/dashboard checks, primary Codex profile registration, Qdrant readiness without spawning it, and an optional timed initialize/list-tools handshake. Missing venv returns remediation. It does not claim every host is connected or inspect every IDE profile.

## Alternatives

A durable acknowledged job queue would make asynchronous store safe but requires a discoverable job/status protocol. Silent LLM work is inconsistent with opt-in configuration. Replacing whole config files is simpler but can break other integrations. Host CLI adapters remain preferred where they safely provide the desired registration contract.

## Consequences

Store can take longer on a cold embedding model, but failure is visible. Configuration backups contain the previous profile and must be treated as private. Atomic writes prevent partial files, not concurrent lost updates. Workspace safety improves for the covered entry points; stress tests and explicit client session binding remain open.

## Validation

MCP durability/input/tag tests, real stdio discovery/annotations, configuration preservation/idempotence fixtures, workspace submission/switch tests and Go/Python lock interoperability. Native client UI/trust and Windows process-tree cancellation require separate execution.

Required client registration, managed rule and hook write failures exit nonzero rather than continuing to a setup-success banner. Missing optional clients still skip, and advisory optional runtime features can degrade with a warning. Installer completion therefore reflects required configuration failures.
