---
title: ADR 0003 — Local MCP and portable hooks
description: Preserve MCP portability while removing shell-dependent automation and redundant provisioning.
---

- Status: Accepted
- Date: 2026-10-23
- Related: [integration strategy](../integrations.md)
- Restructured: 2026-10-25

## Context

The product serves several desktop/IDE clients with different configuration and hook capabilities. Setup repeatedly provisioned the same backend, Codex paths ignored alternate profiles, and hooks embedded unquoted POSIX commands that fail in Windows paths with spaces. ChatGPT desktop guidance had become outdated.

## Decision

Keep local stdio MCP as the baseline. Prepare the shared backend once per setup invocation; honor `CODEX_HOME` for related config/rules/hooks; declare stdio explicitly for VS Code; register detected current ChatGPT desktop through the shared Codex profile.

Invoke a stdlib-only Python hook entry point by absolute path, with quoted interpreter/script/data arguments. Set subprocess environment inside Python, emit structured JSON and avoid interpreting session IDs as filesystem paths. Preserve per-client adapters and their current event coverage; do not claim hook parity merely because MCP registration succeeds.

## Rejected alternatives

A plugin per client would duplicate the memory implementation. An unauthenticated public proxy expands the boundary unnecessarily for local desktop use. Inline shell snippets are concise but hard to quote and validate across hosts. Re-running dependency setup for every adapter adds work without improving registration.

## Consequences

A fresh setup is required to rewrite installed hook commands. Existing session sentinels use the previous naming scheme and are naturally refreshed after search. Hook trust remains a client responsibility. Old ChatGPT versions may lack local MCP support. Host transcript normalization and schema-preserving config edits remain follow-up work. No source watcher or IDE extension is introduced by this decision.

## Invalidation conditions

- If a supported client drops local stdio MCP, the baseline no longer reaches it, and any other transport needs its own boundary decision; an unauthenticated public proxy was rejected.
- If the hook entry point needs a dependency beyond the Python standard library, it can no longer be invoked by absolute path outside the managed environment, and hook invocation must be revisited.

## Validation

Portable hook tests cover JSON responses, session isolation, traversal-like IDs and data paths with spaces/apostrophes. Go tests cover alternate Codex home. Live registration/trust/reload behavior still needs the adapter matrix described in integration documentation.
