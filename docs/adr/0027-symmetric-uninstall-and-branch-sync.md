---
title: "ADR 0027 — Symmetric uninstall and branch synchronization"
description: Disconnect managed clients before runtime removal, preserve memories, and fast-forward dev after main changes.
---

- Status: Amended
- Date: 2026-10-23
- Amends: [ADR 0026](0026-windows-installer-wsl-client-bridge.md) — adds recorded WSL profile cleanup to disable and native uninstall
- Amended by: [ADR 0028](0028-complete-uninstall.md) — memory retention and the ask-to-quit step
- Related: [ADR 0006](0006-durable-mcp-and-preserved-client-config.md), [ADR 0014](0014-main-only-release-packaging.md), [ADR 0025](0025-desktop-owned-background-service.md)
- Restructured: 2026-10-25

## Context

Windows uninstall removed packaged files without disabling client integrations. CLI disable missed Codex/Cursor hooks and managed rules, relied on the Claude executable, and swallowed failures. macOS continued removal after cleanup errors. WSL removal required remembering custom profile flags. CLI uninstall could erase a separate release install when invoked from a checkout. The maintainer also requested publishing through dev and main without dev falling behind.

## Decision

Share error-returning configuration cleanup between disable and uninstall. Remove this runtime's MCP entries directly from supported local configuration files, its hooks, Claude permission and managed rules. Preserve other servers/settings/hooks and user text. Preserve another runtime's same-named registration and associated rules. Standalone generated Cursor/Cline rules are removed only on exact content equality; edited files remain with a diagnostic.

Journal concrete WSL profiles before setup mutations, including user/home overrides. Disable/uninstall consumes this journal and legacy installer selections without scanning unrelated distros. Verify the launcher still references this installation before removing it. Successful items leave the journal; failures remain retryable. Clear installer repair selections after successful disconnection. Journals, backups and uninstall logs are private state excluded from source control, containers and releases.

Windows runs logged cleanup before removing any files or shortcuts and cancels on failure, then removes only its own user PATH segment. macOS also stops removal on cleanup failure and checks symlink ownership. Memories are retained by native uninstallers and now by default in CLI uninstall; explicit `--delete-data` permits CLI erasure. Existing `--keep-data` remains valid. Never delete a different installation's runtime/data. Native shortcuts/apps remain the native uninstallers' responsibility. Users quit the desktop shell before uninstalling so its lifetime-owned API stops; disable also stops the explicit daemon and shared Qdrant.

Push dev before main and leave both at the same commit. A main-triggered workflow fast-forwards dev to the current main head without force. Divergence fails rather than erasing development commits. Packaging and releases remain main-only.

## Rejected alternatives

Keeping best-effort cleanup would leave active hooks pointing to deleted executables. Scanning every WSL user/distro would edit profiles the user never connected and require broader privileges. Silently ignoring unreachable distros would lose the ability to retry. Deleting whole client files or restoring old backups would overwrite unrelated changes. Force-updating dev could lose unmerged work.

## Consequences

Uninstall may require correcting invalid JSON/TOML, making recorded WSL distributions accessible, or repairing a missing CLI before retrying. Cleanup is idempotent but not a transaction across clients: successful items may already be disconnected when another fails. Recovery backups remain inert. Old custom WSL registrations created before journaling require explicit removal with their original flags. Custom native profiles require the original profile environment; hand-written project configurations and hosted connectors are outside setup's managed file scope. CLI uninstall preserves its release/source tree when keeping data; native app/file deletion belongs to the owning installer. Automatic branch sync depends on repository branch-protection permissions and reports divergence for explicit reconciliation.

## Invalidation conditions

- If a supported client offers an API to unregister servers and hooks, use it instead of editing its files.
- If the project stops keeping a dev branch, or branch protection no longer lets the workflow fast-forward it, the branch synchronization part lapses.

## Validation

See the uninstall milestone in [platform validation](../platform-validation.md) for executed native and fixture checks. Native macOS GUI/package uninstall and real client trust/reload remain unverified locally; cross-compilation is not execution evidence.
