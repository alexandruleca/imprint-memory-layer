---
title: ADR 0057 — The Copilot home is GitHub Copilot's store, configuration and hook root
description: Copilot's surfaces share COPILOT_HOME; Imprint registers MCP, instructions and hooks there, learns its session events by their recorded context, keys sessions by their whole id, and registers it as a WSL target.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0037](0037-learn-every-integrated-client-store.md) — stores: Copilot homes
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (ownership rule, unchanged), [ADR 0056](0056-learn-cline-sessions-from-their-manifests.md), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md), [ADR 0026](0026-windows-installer-wsl-client-bridge.md), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md)
- Restructured: 2026-10-25

## Context

VS Code 1.143 made the Copilot harness the default agent harness. That harness is the Agent Host, built on the Copilot SDK; the Local harness is being retired (VS Code 1.143 release notes, and the agent-harness and agent-host documentation). The Copilot harness, the Copilot CLI and JetBrains share one Copilot home: `COPILOT_HOME`, else `~/.copilot` (`%USERPROFILE%\.copilot` on Windows). It holds:

- MCP servers in `mcp-config.json`;
- the always-on instructions in `copilot-instructions.md`;
- hooks in `hooks/*.json`;
- one folder per session in `session-state/<id>/`, with `events.jsonl`, `workspace.yaml`, `vscode.metadata.json`, `plan.md`, `checkpoints/` and `files/`.

An index database, `session-store.db`, is not needed.

Imprint's Copilot adapter predated this:

- **MCP.** It registered in VS Code's user `mcp.json` under `servers`. The Copilot harness reads the Copilot home instead. VS Code 1.143 offers to move a user's `mcp.json` servers into `mcp-config.json` and removes them from `mcp.json` once confirmed.
- **Instructions.** It wrote `User/prompts/imprint.instructions.md` without front matter, so Copilot never applied it.
- **Hooks.** It said Copilot had no hook system. Copilot reads `hooks/*.json` (`{"version": 1, "hooks": {…}}`), and its handlers have `command` (or `bash`/`powershell`) and `timeoutSec`.
  - Event names in PascalCase receive Claude Code's payloads (GitHub's hooks reference). `Stop` carries `session_id`, `cwd`, `transcript_path`, `stop_reason` and `stop_hook_active`, which is what `hook_entry.py stop` reads. `SessionStart` carries `session_id`, `cwd` and `source`.
  - For `SessionStart` only a top-level `additionalContext` is consumed. Imprint's session-start hook prints Claude Code's `hookSpecificOutput.additionalContext`.
  - A `command` runs in PowerShell on Windows and in bash elsewhere.
- **Learning.** `imprint learn` did not read Copilot.

A line of `events.jsonl` is `{id, parentId, timestamp, type, data, agentId?, ephemeral?}`:

- `user.message` data: `content` (as shown to the user), `transformedContent` (the model's XML), attachments, `source` (`user` from VS Code; skills and agents set others, such as `skill-pdf` or `agent-<id>`), `isAutopilotContinuation` and `delivery`.
- `assistant.message` data: `content` (markdown), `toolRequests`, `reasoningText`, `encryptedContent` and `parentToolCallId`.
- Tool activity is in `tool.execution_*` events.
- The working directory is in `session.start` and `session.resume` (`data.context.cwd`, beside `gitRoot`, `repository` and `branch`) and in `session.context_changed` (`data.cwd`).
- Session ids can be strings the SDK caller chose, and the transcript's file name, `events`, is the same for every session.

Under Remote-WSL, the agent host and its hooks run inside the distribution, so its Copilot home is the distribution's `~/.copilot`. No Copilot conversations exist on the machine this was built on. Both Copilot homes hold only `config.json` and logs, and the Copilot CLI is not installed. The fixtures follow the SDK's event schema (`@github/copilot-sdk` 1.0.23-preview.5, copilot-chat 0.71.0).

## Decision

**The Copilot home is the store, the configuration and the hook root.**

- **MCP.** Setup registers `mcpServers.imprint = {"type": "stdio", "command", "args", "env", "tools": ["*"]}` in `<Copilot home>/mcp-config.json`, keeping other servers and settings. It no longer writes VS Code's user `mcp.json`, and disconnect removes Imprint's old entry from it. In the VS Code 1.143.1 bundle:
  - the Local harness discovers `<Copilot home>/mcp-config.json` because `chat.mcp.discovery.enabled` defaults to `copilot: true`, and in a remote window it reads the remote host's file;
  - the Agent Host resolves the same file as its global configuration;
  - the customization migration moves `mcp.json` servers into that file.

  So every supported VS Code reads the Copilot home, and an entry kept in `mcp.json` would duplicate it and bring the migration prompt back after each setup.
- **Instructions.** Setup writes a managed Imprint block into `<Copilot home>/copilot-instructions.md`, keeping the user's text. Disconnect removes the block, and removes the old prompts file only while it is exactly the generated one.
- **Hooks.** Imprint owns `<Copilot home>/hooks/imprint.json`, which Copilot reads beside the user's hook files:
  - `SessionStart` runs `hook_entry.py copilot-session-start` with a 10-second timeout. It prints `{"additionalContext": …}`, the shape Copilot consumes. The `session-start` action keeps Claude Code's and Codex's shape, as `cursor-pre-compact` already keeps Cursor's.
  - `Stop` runs `hook_entry.py stop` with a 120-second timeout and imports the transcript at `transcript_path`, as Claude Code's and Codex's Stop hooks do.
  - On Windows the commands use PowerShell's call operator and quoting; elsewhere they use the existing POSIX quoting.
  - Disconnect deletes the file. Like the instructions, it is protected while another runtime's entry is in `mcp-config.json`.
- **Detection.** Copilot counts as installed when the Copilot home exists, `copilot` is on `PATH`, or VS Code is installed, so CLI-only users are not skipped.

**Store.** Learn reads `<Copilot home>/session-state/*/events.jsonl`. The clients table names the store, on this computer and in a registered WSL profile (ADR 0056).

**Evidence.** A session's recorded working directories are the `cwd` of its `session.start` and `session.resume` contexts and of its `session.context_changed` events, subagents' events included, which is stricter. A session without one belongs nowhere (ADR 0034). `workspace_policy.transcript_allowed` is unchanged.

**Parsing.** Only `user.message` and `assistant.message` count, and their text is `data.content`:

- a user message counts when it has no `agentId`, its `source` is absent or `user`, and it is not an autopilot continuation;
- an assistant message counts when it has no `agentId` and no `parentToolCallId`;
- tool events, reasoning, encrypted content, `transformedContent` and every other event are skipped.

**Identity.** The source key is `conversation/<session id>`, and the id is the session folder's name. The documented layout names the folder after `session_id`, so the key needs no read. The project comes from `derive_project` of the first recorded cwd, as recorded.

**WSL target.** `copilot` is a WSL registration target.

- **Probe.** It reads `copilot=${COPILOT_HOME:-$home/.copilot}` without sourcing startup files (ADR 0026) and records `copilot_home` when that directory exists.
- **Setup.** `imprint wsl setup --target copilot` writes the bridge launcher `<copilot_home>/imprint-wsl`, the `imprint` entry in `mcp-config.json`, the managed instructions block, and `hooks/imprint.json`. The hook commands call `'<launcher>' hook copilot-session-start` and `'<launcher>' hook stop`. The bridge accepts the new action and translates `cwd` and `transcript_path` as it does for the other clients.
- **Learn.** Learn reads `<copilot_home>/session-state` in the distribution's path context.
- **Removal.** It removes the entry, the block, the hooks file and the launcher.

## Rejected alternatives

- **Learn the Local harness store** (`workspaceStorage/*/chatSessions`, `emptyWindowChatSessions`, mapped to folders through `workspace.json`). Its evidence belongs to a storage container rather than a session. Sessions are copied between containers, and one container mixes path contexts (local, WSL, SSH). That fails ADR 0034's recorded-evidence rule and ADR 0037's path contexts. The harness is being retired, and a session handed off to the Copilot harness becomes an `events.jsonl` with its context.
- **Register in both `mcp-config.json` and VS Code's `mcp.json`.** Duplicate servers are disabled by `chat.mcp.collisionBehavior`, and the migration prompt would return after every setup. Supported VS Code versions read the Copilot home anyway.
- **Hooks inline in `<Copilot home>/settings.json`.** That file is the user's, and its handlers would need matching on removal. An Imprint-owned file in the hooks directory is written and removed whole.
- **One SessionStart output with both shapes.** Neither Claude Code's nor Codex's tolerance of an unknown top-level key is documented, and a validation failure would drop their context.
- **Find a missing `transcript_path` from the layout** (`session-state/<session_id>/events.jsonl`). GitHub's reference lists `transcript_path` among `Stop`'s fields. A second way to locate transcripts would not be needed, because learn reads the store.
- **The shell-free `exec`/`args` hook form.** The reference documents it for the Copilot CLI only.
- **Key by `session.start`'s `sessionId` read from the events.** It is the same id. The folder name needs no read and keeps the key when the start event is missing.

## Consequences

- **Not learned.** Local-harness chats are not learned. Neither are Remote-SSH and dev-container sessions, whose Copilot home is on the remote host or in the container and is not registered.
- **Supported versions.** Supported versions are VS Code 1.143 and later, and Copilot runtime 1.0.x.
  - An older VS Code that does not read the Copilot home loses Imprint once disconnect removes the old `mcp.json` entry. Setup no longer refreshes that entry.
  - A user who turns off the Local harness's discovery of the Copilot configuration does not see Imprint there.
- **Owned settings.** Imprint owns `tools: ["*"]` on its entry, so a narrowed tool list is reset by setup.
- **Stop hook.** The Stop hook also runs `cli_extract` and `cli_summarize`, which do not read Copilot events and store nothing for them (ADR 0037 records the same for Codex).
- **WSL.** The WSL listing, the installer's WSL page and Settings show a GitHub Copilot row.

## Invalidation conditions

- If live testing shows `Stop` payloads without `transcript_path` in VS Code's harness or the CLI, the hook import must locate transcripts another way.
- If Copilot stops reading the Copilot home for MCP servers, instructions or hooks, registration must follow.
- If Claude Code and Codex document that they ignore unknown top-level keys in SessionStart output, the separate `copilot-session-start` action could merge into one output with both shapes.

## Validation

- **Python.**
  - `tests/test_learn_stores.py` covers:
    - a Copilot home learned with only the user's typed messages and the main agent's answers. Skill messages, subagent messages, assistant messages inside a tool call, the autopilot continuation, reasoning, encrypted content, tool events and `transformedContent` are left out.
    - a caller-chosen session id, and a session without a context recording nothing;
    - `session.context_changed` into another workspace's root, including a subagent's, as ownership evidence;
    - Linux and Windows cwds naming their projects;
    - a WSL Copilot home resolved through the bridge's translator;
    - the Stop hook's import of an `events.jsonl`, keyed by the session id.
  - `tests/test_hooks.py` checks each client's SessionStart shape, and that a PascalCase `Stop` payload imports its `transcript_path` unless `stop_hook_active` is set. `tests/test_wsl_bridge.py` checks the new action through the bridge.

  The full suite passed on Linux.
- **Go.**
  - `setup_copilot_test.go` checks setup in a disposable home: nothing is written while Copilot is absent. The golden MCP entry, instructions block and hooks file are written beside unrelated servers, user text and user hooks, and a second run changes nothing. VS Code's `mcp.json` is not written, and the client reports connected. Disconnect removes the entry, block and hooks file, the old VS Code entry and the generated prompts file, and keeps an edited prompts file and another runtime's hooks.
  - `internal/hooks` runs the Copilot commands in bash and checks the top-level `additionalContext` and `Stop`'s `{}`.
  - `wsl_test.go` checks the probe with `COPILOT_HOME`, and registration, idempotence and removal of the Copilot WSL layout.
  - `clients_test.go` and `learn_test.go` check the target and its stores.
- **Native Windows,** from WSL through interop, with temporary directories and the installed interpreter used read-only:
  - The Go client, WSL, learn and cleanup tests passed in a cross-compiled Windows test binary.
  - The hooks test ran the PowerShell commands, with spaces and apostrophes in the paths, in Windows PowerShell with the installed interpreter.
  - The cross-compiled CLI's probe of the real Ubuntu profile reported `copilot_home`.
  - `imprint wsl setup --target copilot` and `remove` ran against a disposable `--home` in the real distribution. The registered hook commands, run in bash as Copilot runs them, answered `SessionStart` with `additionalContext` through the bridge. For a transcript without a context, `Stop` skipped the import after the bridge's ownership check.
  - The installed interpreter, in the Ubuntu path context, found, keyed, named, owned and parsed a fixture session in that home.
- **Needs a live, logged-in Copilot:**
  - that Copilot applies the SessionStart context and the instructions file;
  - that `Stop` payloads carry `transcript_path` in VS Code's harness and the CLI;
  - that the WSL Agent Host runs the distribution's hooks and MCP server;
  - real sessions;
  - VS Code, the CLI and JetBrains loading `mcp-config.json`;
  - macOS.
