---
title: Desktop and IDE integrations
description: Current adapters, verified host capabilities and a plan for dependable automatic integration.
---

## Supported approach

An [experimental Chrome capture extension](browser-extension.md) lives on the
`wip/chrome-extension` branch. It uses a separately paired local HTTP capture API
to save pages and selected text. Gmail and document integrations are coming soon.
It is not an automatic host
adapter or a mailbox synchronization connector.

Use local stdio MCP as the default integration contract. Register an absolute interpreter path with `-m imprint` and explicit `PYTHONPATH` and `IMPRINT_DATA_DIR`. The client owns the MCP process; Qdrant is a shared local daemon. Installation and client registration are separate operations: `imprint bootstrap` provisions the backend, and `imprint setup <target>` registers a detected client.

`imprint setup all` is the default setup path. It probes hosts, skips absent ones and prepares the backend once per invocation. It does not establish that any client actually connected: verify tools inside the client after reload/restart.

| Host | Current adapter | Automatic behavior and limits |
| --- | --- | --- |
| Claude Code | `setup.go` | CLI registration, permissions, managed rules and lifecycle hooks. Verify with `claude mcp list` and `/mcp`; hook behavior needs a live client fixture. |
| Claude Desktop | `setup_claude_desktop.go` | JSON registration; OS/MSIX paths and WSL bridge supported in source. Guidance comes through MCP instructions. Conversation ingestion uses explicit exports. |
| Codex CLI / IDE | `setup_codex.go` | TOML MCP registration, managed AGENTS block, SessionStart/Stop hooks. Paths now honor `CODEX_HOME`. `imprint learn` and the Stop hook's conversation import read current rollouts: `session_meta` and `turn_context` working directories, and `response_item` messages. Config does not imply hooks are trusted or every transcript revision is parsed. |
| Current ChatGPT desktop | `setup_chatgpt_desktop.go` | When detected, register through the shared Codex profile. Versions/accounts without local MCP support need an upgrade or in-app capability check. WSL detection reports the Windows app but requires native Windows setup; it does not write Linux interpreter paths into the Windows profile. No public unauthenticated proxy is required. |
| Cursor | `setup_cursor.go` | MCP JSON, managed rule and event adapters. Test hook names, matchers and payloads against actual installed versions. |
| VS Code / Copilot | `setup_copilot.go` | User MCP JSON under `servers`, explicit `type: stdio`, instruction file. Existing adapter does not implement current VS Code hooks. Profiles and remote extensions need separate validation. |
| Cline | `setup_cline.go` | MCP JSON and rules; extension/profile discovery requires fixtures. |
| OpenClaw | `setup_openclaw.go` | Existing registration adapter; review its tool-specific semantics before claiming parity. |

Official OpenAI documentation describes shared MCP configuration for ChatGPT desktop, Codex CLI and the IDE extension, including local stdio, and user/project configuration scopes. This supersedes the old adapter's advice to publish a local server through an SSE proxy. See [OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

Codex hooks have a trust lifecycle and support more events than the adapter's April 2026 comments suggest. Transcript layout is explicitly unstable; JSON output and recursion prevention matter for Stop hooks. Treat the adapter's narrower coverage as an implementation limitation. See [OpenAI hook documentation](https://learn.chatgpt.com/docs/hooks).

Claude Code supports client-managed stdio registration with explicit scope. Use the host CLI where it provides safe configuration updates. See [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp). VS Code uses an MCP `servers` object and a distinct configuration/trust flow; see [VS Code MCP documentation](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

## Windows installation with WSL clients

The Windows installer can register detected default-user Claude Code and Codex profiles in selected WSL distributions. `imprint wsl setup --distribution <name> --target claude-code|codex` also supports an explicit Linux user and alternate profile directories. The selected client runs a private `imprint-wsl` stdio launcher against the existing Windows interpreter and data directory. Rules/hooks use the same bridge, including transcript-path translation; workspace approvals remain mandatory. `imprint learn` reads each registered profile's conversations in the same path context ([ADR 0037](adr/0037-learn-every-integrated-client-store.md)). This does not expose the dashboard/Qdrant listener or provision a second Linux runtime.

Registration is opt-in, backed up and idempotent. The adapter verifies that Windows-executable interoperability works, preserves unrelated MCP/settings/hooks/rules, and supports explicit removal. Client trust/restart and the existing Codex transcript limitations remain separate. Custom shell startup files are not sourced to discover environment overrides. See [installation](installation.md#claude-code-and-codex-inside-wsl), [ADR 0026](adr/0026-windows-installer-wsl-client-bridge.md), and [native validation](platform-validation.md).

## Automation improvements, in order

1. **Extend read-only doctor:** basic `imprint doctor --json [--probe-mcp]` is implemented for Python/dependencies/dashboard, primary Codex registration, Qdrant readiness and timed MCP discovery. Extend it to `imprint doctor --json` reporting executable, architecture, managed interpreter, dependency versions, model availability, storage health, detected client/version, actual registration and a timed MCP initialize/list-tools probe. Distinguish missing, registered, connected and degraded states.
2. **Plan and apply:** add `imprint integrations list`, `plan --target`, `apply` and `remove`. Backups and preservation are implemented in the current helpers; add a preview of exactly the Imprint-owned diff; preserve unrelated settings, comments and custom timeouts. Offer user or project scope explicitly. A detected directory is insufficient evidence of the right profile.
3. **Capability adapters:** share a backend descriptor and keep per-client config schema, hook events, instructions, transcript normalization, trust and reload guidance separate. Record a tested client-version range rather than hard-coded historical claims.
4. **Stable launch command:** move client registrations toward a small `imprint mcp` launcher resolving the current writable runtime. This avoids stale interpreter paths after updates. It must emit protocol traffic only on stdout and startup diagnostics on stderr, with a bounded startup time.
5. **Watcher and workspace binding:** opt-in source refresh with debouncing, ignore rules, stale-file hashes and visible queue state. Bind workspaces per project/client session instead of relying only on a machine-global active workspace. Do not silently index every repository or export.
6. **Client packages:** evaluate Claude desktop extensions and Codex/ChatGPT plugin packages after the underlying launch and health contracts are stable. A package can improve discovery and configuration, but cannot bypass client trust or turn local-only transport into cloud access.
7. **IDE extension only when useful:** a small extension can expose connection health, project selection, file-change refresh and quick search. Retrieval should continue to use MCP so each IDE does not acquire a separate memory implementation.

## Validation contract

Each adapter needs golden fixtures with unrelated existing configuration, a second run proving idempotence, relocation/update, disable/uninstall, spaces and Unicode, alternate home/profile paths, and malformed/permission-denied files. Add a real MCP handshake test and live hook payload fixtures per supported client. Exports remain explicit user-selected imports; avoid claims of passive desktop conversation capture.

Manual verification: run `imprint setup <target>`, reload the host, confirm `imprint` tools appear, search a uniquely indexed fixture, store/retrieve one decision, then confirm removal preserves other servers and memories. Execute this only in disposable profiles during automated tests.

## Current configuration guarantees

JSON edits preserve custom server options/environment. Owned hook edits retain other handlers, including handlers sharing an event/matcher. TOML edits validate input/output and preserve unrelated sections, custom options and nested server tables; formatting within an edited owned stanza can normalize. Changed files receive a private `.imprint-<timestamp>.bak` before an atomic replacement. Invalid or unsupported syntax fails without overwriting the original. JSONC and concurrent edit protection are still open. See [ADR 0006](adr/0006-durable-mcp-and-preserved-client-config.md).

`imprint doctor --json --probe-mcp` initializes the installed server and discovers tools without loading an embedding model or editing profiles. A registered entry is not proof of a host-side trusted connection. The diagnostic currently checks the primary Codex profile, not every client/profile. Missing venv is an actionable failure; a daemon not yet started or a client not detected is distinguished from a broken check.

Required client registration, managed rule and hook write failures exit nonzero rather than continuing to a setup-success banner. Missing optional clients still skip, and advisory optional runtime features can degrade with a warning. Installer completion therefore reflects required configuration failures.
