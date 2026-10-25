---
title: ADR 0052 — Learn Cursor conversations from their folder names
description: A Cursor folder name counts as a conversation's working directory when exactly one existing directory has it; Cursor's profile is a learn store and a WSL registration target.
---

- Status: Accepted
- Date: 2026-10-25
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (amended: evidence), [ADR 0037](0037-learn-every-integrated-client-store.md) (amended: stores), [ADR 0026](0026-windows-installer-wsl-client-bridge.md)

## Context

`imprint learn` did not read Cursor conversations. Cursor's agent writes one transcript per conversation to `~/.cursor/projects/<folder>/agent-transcripts/<id>/<id>.jsonl`. Its subagents' transcripts are one level deeper, in `<id>/subagents/`. The records are `{"role": …, "message": {"content": [...]}}` with text and `tool_use` blocks, plus `turn_ended` status records. Tool results are not recorded. A user message wraps what the user typed in `<user_query>`, after a `<timestamp>` and any attached image or document lists. The id is a random UUIDv4.

Two rules blocked learning them:

- **No working-directory evidence.** ADR 0034 learns a conversation only from the working directories it recorded, and never takes an encoded folder name as evidence. Cursor's records state no cwd, session id or timestamp field. Its only link to a directory is `<folder>`, the directory's path with every run of other characters than ASCII letters and digits turned into `-` and none left at either end (`/home/me/code/shop` gives `home-me-code-shop`, `C:\Users\me` gives `C-Users-me`). That encoding is lossy: `/x/a-b` and `/x/a/b` share a name. The other sources considered are listed under Alternatives.
- **The store is in an unregistered WSL profile.** With Cursor's Remote-WSL, the Windows app runs the agent against the distribution, and the transcripts are written to the distribution's `~/.cursor`. Imprint's Cursor setup (`mcp.json`, `hooks.json`, the rule) lives in the Windows profile, whose `projects/` holds no transcripts on such a machine. ADR 0037 reads only registered WSL stores, and the registration targets were `claude-code` and `codex`.

## Decision

**Evidence.** A client folder name counts as a recorded working directory when exactly one existing directory has it. `transcripts.folder_directory` walks the filesystem from its root. It enters only directories whose own folder name begins what is left of the name, then a `-`. The match is the entry whose folder name is the rest. These leave the name standing for no directory:

- no match;
- several matches;
- a match that is not a directory, such as a multi-root `.code-workspace` file;
- a candidate directory that cannot be listed, because uniqueness is then unknown.

A conversation whose folder names no directory belongs to no workspace, which is ADR 0034's rule for conversations without evidence. The resolved directory then goes through `workspace_policy.transcript_allowed` and `derive_project` unchanged. The walk uses the client's paths. Under a WSL path context, Linux paths are listed through the bridge's translator (`wsl_paths.native_path`), so `\\wsl.localhost\<distro>\…` and drive mounts resolve as they do for recorded cwds. On native Windows the walk starts from the drive letters and compares names without case. Each folder name is resolved once per process. `transcripts.cwds(path)` is the single source of a transcript's recorded working directories for ownership and naming. It returns record cwds for Claude Code and Codex, and the resolved folder for a transcript filed under `agent-transcripts`.

This amends ADR 0034. Claude Code's folder names are still never evidence, because its records state the cwd. A Cursor folder name is evidence only when the filesystem says which directory it is.

**Stores.** This computer's Cursor profile, `~/.cursor`, is a learn store. `cursor` is a WSL registration target. The profile probe records `cursor_home` (`$HOME/.cursor`) when it exists, and the installer's **Connect clients in WSL** page lists it like the other targets. `imprint wsl setup --target cursor` journals the registration and writes nothing into the profile. Cursor's agent there is driven by the Windows app, whose MCP server and hooks come from `imprint setup cursor`. Removal and disable drop only the record. Learning still reads registered stores only, as ADR 0037 requires.

**Parsing and identity.**

- Discovery: `projects/*/agent-transcripts/*/*.jsonl`. Subagent transcripts are skipped, as Claude Code's sidechains are. Their prompts are written by the agent, not the user.
- Roles come from `role`. Only text blocks count.
- A user message's text is the content of its `<user_query>` blocks. A message with none, such as a bare timestamp, contributes no text.
- The source key is `conversation/<first eight characters of the id>`, the rule for Claude Code's random session ids.
- The project name comes from the resolved directory, so it matches a Claude Code or Codex session in that directory.
- Conversation text is never rewritten. Cursor's `[REDACTED]` placeholders stay as written.

## Alternatives

- **Decode the folder name into a path, or match encoded roots against it.** Hyphens in directory names make decoding guesswork. Prefix matching leaks between workspaces: the encoded root `x-a` also matches `/x/a-b`.
- **A Cursor-only evidence parameter on `transcript_allowed`.** This is a second evidence channel beside recorded cwds.
- **Cursor's `workspaceStorage` (`state.vscdb` → `composer.composerData`).** It maps conversation ids to folder URIs, but it is SQLite on the UI host while the transcripts are in WSL. One conversation on this machine was listed under two workspaces.
- **`terminals/*.txt` front matter or shell `working_directory` arguments.** Terminal state changes and many projects have none. Only 7 of 1060 tool calls carried a directory.
- **Cursor's hooks, which now receive `workspace_roots` and `transcript_path`.** They are reliable evidence, but only at hook time and only for new conversations, and learning would need a stored mapping. Live import can be added on top of this decision; it cannot cover past conversations.
- **Read each registered WSL home's `.cursor` whenever Cursor is set up on Windows.** This reads a store nobody registered, and it ties a Linux read to a Windows integration.
- **Write MCP configuration and hooks into the WSL `~/.cursor`.** Cursor does not run its agent from there; the Windows app does.

## Consequences

Cursor conversations are learned on every platform, and in WSL once the distribution's Cursor profile is registered. That happens through the installer's WSL page on install, repair or update, or with `imprint wsl setup --distribution <name> --target cursor`. Silent updates replay only saved choices, so existing installations do not register Cursor implicitly.

Some conversations are not learned:

- those from directories that were deleted or renamed;
- those from multi-root workspace files;
- Cursor's empty-window and temporary folders, which name no directory.

A directory created later under a colliding name makes a folder ambiguous. Its conversations then stop being learned, and memories already learned stay. A directory whose name has no letter or digit vanishes from folder names and is not entered. If Cursor keeps non-ASCII letters in folder names, those folders resolve to nothing.

Resolution costs one directory listing per path segment for each candidate, once per folder per run.

Cursor's Stop hook is still not wired, so conversations reach memory through `imprint learn` or the dashboard's **Learn** action only. The comment in `setup_cursor.go` that says Cursor's hook payload omits `transcript_path` is out of date.

## Validation

- Python: `tests/test_learn_stores.py`.
  - A Cursor store is learned with only the user's query and assistant text. Timestamps, attachment lists, tool input and subagent transcripts are left out, and a conversation whose directory is gone is counted as not this workspace's.
  - Folder resolution: unique directories, hidden directories, separator ambiguity, a workspace file, a missing directory, a numeric folder, and an unlistable candidate.
  - Ownership by a resolved directory, including one another workspace claimed and one that names no directory.
  - A WSL Cursor store walked through the bridge's translator, which names the project like native learning.

  The full suite passed on Linux (348 tests).
- Go: `cmd/learn_test.go` covers this computer's Cursor store and a registered WSL Cursor store in the distribution's group. `cmd/wsl_test.go` covers probing `cursor_home`, a Cursor registration that writes nothing, a profile without Cursor being refused, and disable dropping only the record. `go build ./...`, `go vet ./...` and `go test ./...` passed on Linux. The learn, WSL and cleanup tests passed in a cross-compiled Windows test binary.
- Real stores on this machine, read-only, using a temporary data directory: see [platform validation](../platform-validation.md#learn-from-cursor-stores-2026-10-25).
  - WSL Cursor store: 4 conversations, each resolved to its directory and named `arnia-gmv-reporting-service`, `airportlabs-challenge` and `arnia-shopware`. Temporary, numeric and workspace-file folders named no directory. This held natively and through the installed Windows interpreter over `\\wsl.localhost`.
  - Native Windows folder names resolved by drive without case.
  - The cross-compiled CLI's `wsl list` reported `cursor_home` for the real Ubuntu profile.
- Not run:
  - `imprint wsl setup --target cursor` against the installation, so its journal was not written;
  - the installer wizard and `imprint-setup.ps1` with a saved Cursor selection;
  - a complete `imprint learn` storing Cursor conversations into a real data directory;
  - macOS Cursor stores.
