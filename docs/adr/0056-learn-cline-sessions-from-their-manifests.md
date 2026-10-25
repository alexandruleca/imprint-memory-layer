---
title: ADR 0056 — Learn Cline sessions from their manifests
description: A Cline session's manifest is its working-directory evidence; Cline's sessions directory is a learn store, setup registers where Cline 4 reads, and cline is a WSL registration target. No hook is wired.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0037](0037-learn-every-integrated-client-store.md) — stores: Cline sessions directories
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (ownership rule, unchanged), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md), [ADR 0026](0026-windows-installer-wsl-client-bridge.md), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md)
- Restructured: 2026-10-25

## Context

Cline extension 4.0.0 (2026-06-26) and Cline CLI 3.0.0 moved every Cline surface onto the Cline SDK: the VS Code extension and its forks, the CLI, the JetBrains plugin and the desktop app. The SDK keeps one data directory for all of them: `CLINE_DATA_DIR`, else `CLINE_DIR/data`, else `~/.cline/data` (`%USERPROFILE%\.cline\data` on Windows). Source: `sdk/packages/shared/src/storage/paths.ts` at cline/cline@40c5d99 (extension 4.1.25, CLI 3.0.73).

Imprint's Cline adapter predated the SDK:

- **MCP registration.** It wrote VS Code's `globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`. Cline 4 imports that file once (`mcp-settings-legacy-migration.ts`) and from then on reads only `CLINE_MCP_SETTINGS_PATH`, else `<data dir>/settings/cline_mcp_settings.json`.
- **Rule location.** It wrote the rule to `~/.clinerules/imprint.md`. `.clinerules` is a workspace folder: the rule applied only in a workspace opened at the home directory. Cline's global rules directories are `<CLINE_DIR>/rules`, `~/Cline/Rules`, `~/Documents/Cline/Rules`, plus `~/.agents/AGENTS.md`.
- **Merged entries.** It merged Imprint's flat stdio entry into an existing one. `cline mcp add` writes the nested form `{"transport": {"type": "stdio", …}}`. Cline's settings schema tries the nested form first and drops unknown keys, so after a merge the entry kept launching the old transport.
- **Hook message.** Setup printed "Cline has no hook system". Cline has hooks (TaskStart through TaskComplete, and SessionShutdown), but their payloads carry no transcript path.
- **Learning.** `imprint learn` did not read Cline.

A Cline 4 session lives in `sessions/<id>/`:

- `<id>.json` is the manifest. It holds `session_id`, `source`, `started_at`, `status`, `cwd`, `workspace_root`, `metadata` (title, history origin, import record) and `messages_path`.
- `<id>.messages.json` is one JSON document, not JSON lines: `{"version": 1, "sessionId", "agent", "messages": […]}`.
- Subagents and teammates write `<agentId>.messages.json` and `<agentId>__<teamTaskId>.messages.json` in the same folder.

`cwd` and `workspace_root` are exact absolute paths written when the session is created. VS Code records its first workspace folder, Windows records Windows paths, and a session without a folder runs in Cline's chat workspace, `<data dir>/workspaces/chat`. Session ids are `<epoch ms>_<5 base36 characters>`, so sessions started in the same millisecond share their first 13 characters.

Under Remote-WSL or Remote-SSH, the extension runs on the remote host, so its settings and sessions are in that host's `~/.cline`. The WSL registration targets were `claude-code`, `cursor` and `codex`.

Tasks recorded by the extension before the SDK (globalStorage `tasks/<id>/`) state no reliable working directory. The maintainer decided not to learn them.

No Cline data exists on the machine this was built on. The fixtures follow the schemas above and the SDK's own session fixture.

## Decision

**Store.** Learn reads Cline's sessions directory: `CLINE_SESSION_DATA_DIR`, else `<data dir>/sessions`, resolved as Cline resolves it. Cline trims each variable and treats an empty one as unset.

- Discovery takes `<id>/<id>.messages.json` only. Subagent and teammate files are skipped, as Claude Code's and Cursor's subagent transcripts are: their prompts come from the lead agent.
- The clients table names each client's learn store: `store` on this computer, `wslStore` in a registered WSL profile. Learn's list of this computer's stores now comes from the table instead of a separate list in `learn.go`.

**Evidence.** A session's recorded working directories are its manifest's `cwd` and `workspace_root`. `transcripts.cwds` reads them from the manifest beside the messages file and never follows `messages_path`. A missing or unreadable manifest gives no evidence, so the session belongs to no workspace, which is ADR 0034's rule. `workspace_policy.transcript_allowed` is unchanged.

**Parsing.** The records of a messages file are its document's `messages`, and only text blocks count.

- Messages Cline's own transcripts hide are skipped: display-only errors, and messages whose `displayRole` is `system`, `status` or `error`, such as hook output and compaction summaries.
- A user message's text is what Cline shows as the user's message (`formatDisplayUserInput`):
  - the content of `<user_input mode="…">`;
  - a `<user_command slash="x">` as `/x …`;
  - `<mode_notice>` elements removed, and attachments outside the wrapper left out.
- Text without a wrapper is the typed text. The SDK's own session fixture, and sessions written before the wrappers, look like this.
- Prompts the runtime sends as the user contribute no text: hook context, task resumption and the plan-to-act continuation. The assistant's reply then stays with the user's question.

**Identity.** The source key is `conversation/<full session id>`, and the id is the session folder's name. The project comes from `derive_project` of the manifest's `cwd` as recorded, before translation. Chat-workspace sessions have no project.

**Imports.** Cline can import Claude Code, Codex and OpenCode sessions. A session marked as imported is skipped, because the original store owns that conversation. The mark is `importedFrom` in the manifest's metadata, or a history origin of mode `import`.

**Setup.**

- Imprint registers in `CLINE_MCP_SETTINGS_PATH`, else `<data dir>/settings/cline_mcp_settings.json`. Every Cline 4 surface reads that file.
- Cline counts as installed when its directory or data directory exists, or when VS Code has the extension's storage directory.
- The globalStorage file is no longer written. Disconnect still removes Imprint's entries from it.
- Imprint's entry stays in the flat stdio form that every Cline version reads. The entry Imprint owns must not have a `transport`. In the shared JSON helper, a nil value in Imprint's spec now means "remove this key". The merge removes a nested transport and keeps the entry's other settings, such as `disabled` and `timeout`.
- The rule is written to `<CLINE_DIR>/rules/imprint.md`. Disconnect removes the old `~/.clinerules/imprint.md` only when its content equals the generated rule.

**WSL target.** `cline` is a WSL registration target, because Cline runs inside the distribution.

- **Probe.** It reads `cline=${CLINE_DIR:-$home/.cline}` in the non-login shell (no startup files are sourced, ADR 0026) and records `cline_home` when that directory exists.
- **Setup.** `imprint wsl setup --target cline` writes the bridge launcher `<cline_home>/imprint-wsl`, the `imprint` entry in `<cline_home>/data/settings/cline_mcp_settings.json` and the rule `<cline_home>/rules/imprint.md`.
- **Learn.** Learn reads `<cline_home>/data/sessions` in the distribution's path context.
- **Removal.** It checks that the entry still points to the launcher, then removes the entry, the generated rule and the launcher.

Supporting changes:

- `imprint wsl setup` and `remove` describe each target's files once (`wslLayout`) instead of branching on target names.
- A WSL registration record is compared by the registered client's own directory. A probe that now also reports `cline_home` therefore does not turn existing Claude Code, Codex or Cursor records into different registrations.
- `imprint wsl setup` checks its target before probing.
- The installer's replay of saved WSL choices leaves the target check to the CLI and no longer keeps its own target list. The wizard's empty-state text names no clients.

**No hook.** Cline's hook payloads carry no transcript path, so no hook is wired. Setup says that conversations arrive through `imprint learn` or the dashboard's **Learn** action.

## Rejected alternatives

- **Parse `<environment_details>` or other workspace paths in message text.** This text is written for the model, many messages lack it, and it can name directories other than the session's. It is a guess, not recorded evidence.
- **Learn legacy tasks from their `cwd` fields** (`taskHistory` items, `cwdOnTaskInitialization`). These are not reliable, and the maintainer excluded legacy tasks. ADR 0034 is unchanged.
- **Import through a Cline hook** (TaskComplete or SessionShutdown). The payloads carry no transcript path, so the session would have to be found by id. Only new sessions would be covered, and learn still needs the store for past ones.
- **Replace Imprint's whole entry instead of removing `transport`.** This is simpler, but it discards `disabled` and `timeout` set in Cline. Setup and repair would turn Imprint back on after the user turned it off, against ADR 0006.
- **Write the nested `transport` form.** Cline's CLI before 3.0 reads only the flat form.
- **Keep writing the globalStorage file for Cline 3.x.** This is a second registration path for a version that Cline 4 has replaced.
- **Read the session index (`db/sessions.db`).** It is SQLite and holds the same data as the manifests, which are files beside the messages.
- **Key sessions by their first eight characters, as Claude Code and Cursor are.** All sessions started in the same 100 seconds would then share a key.
- **Honor `CLINE_DATA_DIR` or `CLINE_SESSION_DATA_DIR` in a WSL profile.** Discovering them would mean sourcing shell startup files, which ADR 0026 rejects.

## Consequences

- **Versions.** Supported versions are Cline extension 4.0 and later, and Cline CLI 3.0 and later.
  - Tasks from earlier versions are not learned.
  - An extension older than 4.0 gets no new MCP registration. An existing globalStorage entry stays until disconnect removes it.
  - The old `~/.clinerules/imprint.md` stays until disconnect removes it.
- **Multi-root workspaces.** A multi-root VS Code workspace records its first folder only. Its sessions are owned and named by that folder.
- **Forks.** A forked session starts with a copy of its parent's messages. Both are learned, each under its own id.
- **Chat workspace.** Sessions without a folder are in a directory no workspace claims. The workspace that runs learn learns them, without a project.
- **Changed environment.** Disconnect reads the current environment. A registration written under another `CLINE_DIR`, `CLINE_DATA_DIR` or `CLINE_MCP_SETTINGS_PATH` value is not found.
- **WSL.** The listing and the installer's WSL page show a Cline row for each distribution. Settings shows it without a dashboard change. Remote-SSH hosts are not registered.
- **Registration targets.** An invalid saved target now fails in the CLI with its message (`target must be …`) instead of in the setup script.

## Invalidation conditions

- If Cline changes its session layout or stops writing manifests with `cwd` and `workspace_root`, evidence and parsing must follow.
- If Cline's hook payloads gain a transcript path, a hook import of new sessions could be added.
- If Cline CLI versions before 3.0 no longer need support, Imprint's entry could use the nested `transport` form.

## Validation

- **Python.** `tests/test_learn_stores.py` covers:
  - a Cline store learned with only the user's typed text and assistant text. Mode notices, attachments, thinking, redacted thinking, tool use and results, flagged and unflagged hook context, the plan-to-act continuation and display-only errors are left out, and a slash command reads as `/name …`.
  - subagent and teammate files skipped;
  - imported sessions skipped, finished and in progress;
  - a session without a manifest counted as not this workspace's;
  - two sessions with the same millisecond prefix keeping distinct keys;
  - unwrapped text, an empty command and a wrapped task resumption;
  - manifest `cwd` and `workspace_root` as ownership evidence, including a workspace root another workspace claimed, and a subagent file recording nothing;
  - a Windows cwd naming its project;
  - a chat-workspace session learned without a project;
  - a WSL store resolved through the bridge's translator, naming the project like native learning.

  The full suite passed on Linux.
- **Go.**
  - `setup_cline_test.go` checks setup in a disposable home. With Cline absent, nothing is written. `CLINE_DIR`, `CLINE_DATA_DIR` and `CLINE_MCP_SETTINGS_PATH` are honored. A stale nested transport is removed while `disabled` stays, and a second run changes nothing. Nothing is written to `~/.clinerules` or the extension's storage, the client reports connected, and disconnect removes the entry and the rule.
  - `cleanup_test.go` checks that disconnect removes the current entry and rule, the old generated `~/.clinerules` rule and globalStorage entries, and keeps an edited old rule. A foreign runtime's entry still protects the shared rule.
  - `wsl_test.go` checks the probe and its `CLINE_DIR`, and the Cline WSL registration: launcher, entry, rule, no hooks, preserved settings and a user rule, idempotence, removal, and refusal for an entry that points elsewhere.
  - `clients_test.go` checks the WSL targets and stores. `learn_test.go` checks this computer's Cline store with a `CLINE_DATA_DIR` override, and a registered WSL Cline store.
  - The jsonutil test covers nil spec values.
  - `go vet ./...` and `go test ./...` passed on Linux.
- **Native Windows.** These ran from WSL through interop, with temporary directories and the installed interpreter used read-only.
  - The Cline, WSL, learn, cleanup and client Go tests passed in a cross-compiled Windows test binary.
  - The cross-compiled CLI's probe of the real Ubuntu profile listed the `cline` target.
  - `imprint wsl setup --target cline` and `remove` ran against the real distribution with a disposable `--home`. The launcher's probe ran the Windows interpreter from WSL.
  - The installed interpreter, in the Ubuntu path context, found, keyed, named, owned and parsed a fixture session in that home.
  - The Windows PowerShell 5.1 setup regression replayed a saved `cline` selection, which the old target list would have rejected.
  - The wizard pages compiled with Inno Setup.
- **Not run:**
  - a real Cline (VS Code extension 4.x, CLI 3.x, JetBrains, desktop) loading the settings and rule;
  - real Cline sessions;
  - a WSL registration of a real Cline profile, and the installer wizard with a Cline selection;
  - a complete `imprint learn` storing Cline sessions;
  - macOS.

  [Platform validation](../platform-validation.md#cline-setup-and-learn-2026-10-25) records what ran natively.
