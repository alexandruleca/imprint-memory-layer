---
title: ADR 0026 — Windows installer registration for WSL clients
description: Connect selected WSL Claude Code and Codex profiles to one Windows runtime through local stdio.
---

- Status: Amended
- Date: 2026-10-23
- Amended by: [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) — disable and native uninstall clean up recorded WSL profiles; the manual removal path remains available
- Related: [ADR 0003](0003-local-mcp-and-portable-hooks.md), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md), [ADR 0024](0024-enforce-local-service-boundaries.md)
- Restructured: 2026-10-25

## Context

The Windows installer configures detected Windows clients, but Claude Code and Codex launched inside WSL read Linux profiles. Existing WSL-aware registration implements the opposite direction for Windows desktop clients. Installing another complete Linux runtime would create a second memory store and duplicate dependencies; exposing the dashboard or Qdrant on a public interface would weaken the loopback boundary.

## Decision

Offer an opt-in WSL profile page after installer files are copied and before runtime provisioning/registration completes. The Windows CLI enumerates registered distributions with a bounded `wsl.exe` command, probes default-user profile directories with non-login `sh`, skips Docker/Rancher utility distributions, and lists detected Claude Code/Codex profiles. Discovery may start distributions. WSL subprocesses use `CREATE_NO_WINDOW`; actual Windows execution showed that `DETACHED_PROCESS` loses redirected discovery output. No WSL/client installation is attempted. Fresh silent installs do not edit WSL profiles; repair and silent updates replay explicit saved selections from `.wsl-integrations.txt`. Selected failures are setup failures, not an unqualified success.

Use `imprint wsl list`, `setup` and `remove` as explicit Windows-side operations. Manual setup supports a selected Linux user, home, `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and Windows interpreter. Do not infer settings exported only by interactive shell startup files. Preserve the distinction between default Claude's `~/.claude.json` and an explicit config directory's `.claude.json`, including an override equal to the default directory.

Write a private executable `imprint-wsl` shell launcher into each selected Linux client profile. Register it for stdio MCP, managed usage rules and the existing adapter's lifecycle hooks. Reuse the backed-up, atomic JSON/TOML helpers; retain other servers, custom options, rules and unrelated hook handlers. Reject an unrecognized launcher at the reserved path and malformed profile documents. Verify the launcher can actually execute the Windows interpreter before registering it. An existing Linux Imprint registration under the name `imprint` is explicitly replaced by the selected Windows connection; it is not merged into a second backend.

The launcher resolves the Windows interpreter with WSL's `wslpath` and executes the stdlib-only Windows `imprint.wsl_bridge` script. Pass source/data/CLI paths as individual quoted arguments; keep stdin/stdout as protocol pipes. Remove the script directory from Python's import search path so the project's `queue.py` cannot shadow stdlib `queue`. The shared Windows interpreter, data directory, device identity and managed Qdrant remain unchanged; no new network service or Linux Python install is required.

Carry Linux cwd/distribution context alongside the Windows runtime context. Translate explicitly absolute Linux filesystem paths into the selected distro's `\\wsl.localhost\<distro>` namespace for Linux files. Capture Windows drive mounts from the distro's kernel mount table and map those paths to real drive paths; the UNC provider cannot access those mounts reliably. Do not assume an automount prefix. Profile edits resolve paths with `wslpath -w` as well. Translate hook transcript metadata and policy checks on recorded transcript cwd; never rewrite conversation text or relative source keys. Project approval and overlap checks still apply, and setup does not approve or index roots. Return WSL-native local path hints only after the existing root/cwd/file checks succeed. Unsupported Windows-representation paths fail instead of being guessed. An intentionally narrow `cli` bridge supports indexing/workspace/configuration/diagnostic commands and passes its Windows interpreter to child CLI operations; installation and updates remain Windows-side operations.

Explicit removal verifies that the profile's MCP entry still points to its bridge, removes its owned hook commands/rule block/launcher, and keeps other configuration and memories. Default-profile removal also removes the corresponding saved installer selection. Deselection in the installer affects future add/repair work; it does not unregister an existing connection. Windows uninstall does not silently modify Linux profiles; remove those connections before uninstalling the runtime.

## Rejected alternatives

A second WSL installation with pairing can support interop-disabled systems, but duplicates models, runtime and storage. Editing only MCP configuration omits transcript paths and hooks. A public TCP proxy adds authentication and network complexity to a local integration. Sourcing interactive login scripts to discover custom variables can execute arbitrary user startup commands during installation. Hand-written whole-profile replacements discard unrelated integrations.

## Consequences

This feature requires WSL Windows-executable interoperability and distribution access for the current Windows account. Linux file access uses the WSL UNC provider and Windows drive mounts use their native drive paths; performance and Windows filename restrictions apply. Different filesystem aliases can require explicit root approval; this is not automatic workspace migration. Configuration presence does not establish that a client has loaded or trusted MCP/hooks. Codex transcript format coverage remains the existing adapter's limitation. Custom profiles/users use the manual flags; the wizard targets default-user detected profiles. Backups can contain private client settings and stay outside release payloads.

## Invalidation conditions

- If WSL interop with Windows executables is disabled or removed in supported setups, the bridge cannot work; a second Linux runtime with pairing, rejected for duplication, becomes the alternative to evaluate.
- If clients inside WSL can start a Windows-hosted stdio server without a launcher, the `imprint-wsl` launcher can go.
- If the WSL UNC provider reaches Windows drive mounts reliably, the mount-table translation can be simplified.

## Validation

Go fixtures exercise UTF-16 enumeration, explicit distro selection, utility distro exclusion, quoting, profile preservation, idempotence, removal, malformed TOML and foreign launcher protection. Python tests exercise path context, metadata-only translation and unchanged workspace-root enforcement. Native Windows Go execution and WSL-to-Windows smoke use disposable Linux homes and Windows data, an existing Windows interpreter read-only, real hooks and a 16-tool MCP initialize/discovery round trip. The native setup fixture checks saved selections and stderr/exit behavior. Native Inno Setup compilation checks the actual installer page. Retain exact executed results and GUI/client-trust limitations in [platform validation](../platform-validation.md).

Uninstall lifecycle follow-up: [ADR 0027](0027-symmetric-uninstall-and-branch-sync.md) adds recorded WSL profile cleanup to disable/native uninstall. The original manual removal path remains available.
