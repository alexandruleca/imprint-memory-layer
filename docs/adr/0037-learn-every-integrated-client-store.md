---
title: ADR 0037 — Learn from every integrated client store
description: Learning reads this computer's Claude Code and Codex profiles and every registered WSL profile, each in the path context its hooks use.
---

- Status: Accepted
- Date: 2026-10-24
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) (ownership rule, unchanged), [ADR 0026](0026-windows-installer-wsl-client-bridge.md) (WSL bridge and path translation), [ADR 0003](0003-local-mcp-and-portable-hooks.md)

## Context

`imprint learn`, and the dashboard's **Learn** action, which queues the same command, read only `~/.claude/projects` of the account the runtime runs as. That missed several stores Imprint is integrated with:

- `CLAUDE_CONFIG_DIR` was ignored.
- On a Windows installation, `~` is the Windows profile. The Claude Code and Codex profiles that the installer registered in WSL (ADR 0026) were never read. On an affected machine, the queued learn job printed "No Claude Code projects found" and "No transcripts found" while `.wsl-registrations.json` recorded both Ubuntu profiles.
- Codex sessions were never learned. Store discovery did not look for them, and the transcript parser read only Claude Code records. Codex records its working directory inside the `payload` of `session_meta` and `turn_context` records, so the ownership rule found no evidence, and the Stop hook's import of a Codex rollout stored nothing.

Two identity problems followed from where a transcript was found rather than what it records:

- The project name came from splitting the transcript path on `/`. A transcript reached as `\\wsl.localhost\<distro>\…` got no project, and Codex files rollouts by date, not by project.
- The source key was `conversation/` plus the first eight characters of the file name. That is `rollout-` for every Codex session. Codex session ids are time-ordered, so even their first eight characters are a timestamp that sessions started within about a minute share.

## Decision

**Stores.** Learning reads the stores of the clients Imprint is integrated with:

- This computer's Claude Code profile (`CLAUDE_CONFIG_DIR`, else `~/.claude`): transcripts in `projects/*/*.jsonl` and auto-memory files in `projects/*/memory/*.md`.
- This computer's Codex profile (`CODEX_HOME`, else `~/.codex`): rollouts in `sessions/**/rollout-*.jsonl` and `archived_sessions/rollout-*.jsonl`.
- Each WSL registration in `.wsl-registrations.json`: the registered target's store at its recorded directory (`claude_home` for Claude Code, `codex_home` for Codex) for the recorded distribution and user. Setup journals these records before it changes a profile, and repair and update replay installer selections into them. Learning does not probe unregistered distributions and does not source shell startup files.

**Path context.** Stores are grouped by path context, and one learner process (`imprint.cli_conversations --store <client> <dir> …`) runs per group. A WSL group runs with `IMPRINT_WSL_DISTRO` and `IMPRINT_WSL_MOUNTS`, the context the bridge gives that distribution's hooks. The mount table comes from the distribution's kernel mount table, read through `wsl.exe` as the registered user with the shell snippet the launcher uses. The store directory and every recorded cwd then resolve through `wsl_paths.native_path`, the bridge's translator. Linux paths map to `\\wsl.localhost\<distro>\…` and Windows drive mounts map to drive paths. This computer's group states an empty context, so a learn started through the bridge does not apply its caller's distribution to Windows stores. Conversation text is never rewritten.

**Ownership.** ADR 0034 applies unchanged through `workspace_policy.transcript_allowed`. Codex's recorded working directories, the `cwd` of `session_meta` and `turn_context` records, count as recorded cwds like Claude Code's per-entry `cwd`. Both formats are read by `imprint/transcripts.py`, which learning and the ownership check share. The check resolves each distinct recorded cwd once. Claude Code repeats the cwd on nearly every record, and through `\\wsl.localhost` each resolution is a filesystem round trip of about 20 ms. Resolving per record made the ownership read of a WSL transcript about 30 times slower than reading it.

**Identity.** A conversation's project comes from its first recorded cwd as recorded, before translation. The existing naming rule is applied to Claude Code's folder encoding of that cwd (every character other than a letter or digit becomes `-`). That encoding is the folder name the rule used before, so Claude Code project names do not change. A WSL profile learned through the Windows runtime gets the same names as the same profile learned natively, and a Codex session gets the same name as a Claude Code session in the same directory. The Stop hook's import uses the same derivation. Auto-memory files keep their existing folder-name rule: changing it would store their content again under new names.

**State.** The source key stays the conversation's identity. Claude Code keeps `conversation/<first eight characters of the session id>`. Codex uses `conversation/<session id>` from its `session_meta` record. A conversation found through more than one store in one run is read once, from the newest copy. Across runs, its stored source modification time skips it or replaces its chunks. Memory ids hash project, source and content, so the same conversation reached through different paths gets the same ids.

**Failures are reported.** These conditions are reported, and the other stores are still learned:

- A distribution whose mount table cannot be read (unknown distribution, WSL unavailable, timeout) is skipped with the `wsl.exe` message. `wsl.exe` reports its own failures on standard output, and the WSL runner now includes that text.
- A profile that exists but cannot be listed is reported by the learner.
- A missing profile directory is reported as absent.

Learn ends with a "Not learned" line for each problem and exits successfully. Reading a mount table can start a stopped distribution, as WSL discovery already does (ADR 0026).

## Alternatives

- **One process with a path-translation parameter on `transcript_allowed`.** This adds a second path-context channel beside the environment the bridge already sets for the same code.
- **Have the Go CLI pass `\\wsl.localhost` paths.** This is a second translator, and it ignores drive mounts.
- **Probe every distribution for profiles at learn time.** This reads clients the user did not integrate and starts every distribution.
- **Read `/proc/mounts` through `\\wsl.localhost\<distro>\proc`.** Its contents through the 9P server are not the table the bridge receives.
- **Name conversation projects from manifests, as file ingestion does.** This would align conversation and code project names, but it renames every learned conversation. That is a separate decision.
- **Key Claude Code conversations by their full session id as well.** This would re-key and duplicate every Claude Code conversation already learned.
- **Fail the run when a store is unreachable.** A registration for a removed distribution would then fail every learn and every dashboard job until it is removed.

## Consequences

Windows installations with WSL registrations learn those profiles' Claude Code and Codex conversations. Codex conversations are learned on every platform, both by `learn` and by the Stop hook's conversation import. The Stop hook's extraction (`cli_extract`) still reads only Claude Code records. Its summarizer (`cli_summarize`) still expects an older Codex event format. Both are unchanged here. Archived Codex sessions are learned too: archiving hides a thread in Codex but keeps its rollout.

Resolving each distinct cwd once also shortens the WSL bridge's Stop check, which runs the same ownership read on every turn. Reading through `\\wsl.localhost` is still slower than local disk. The modification-time check now runs before the ownership read, so an unchanged conversation costs a `stat` and its first records. An auto-memory folder still reads every transcript beside it on each run (ADR 0034's rule, unchanged). The learner reads a WSL profile as the Windows account through the WSL file provider, so a custom user's profile must be readable that way, as in ADR 0026.

Native Windows and macOS working directories (`C:\…`, `/Users/…`) still get no project name under the existing rule. `imprint learn` prints one step per path context with per-store counts. `cli_conversations --all` is replaced by `--store`, and the auto-memory import moved from a script embedded in the Go CLI into `cli_conversations`.

## Validation

- Python: `tests/test_learn_stores.py` uses fixture stores. It covers:
  - Claude Code transcripts, auto-memory and skipped subagent files, and Codex sessions and archived sessions, with only user/assistant text kept.
  - Codex sessions that share an eight-character prefix keeping separate source keys.
  - A WSL store resolved through the bridge's translator, including a drive-mounted cwd claimed by another workspace, with the project named from the recorded Linux cwd.
  - A conversation reachable through two stores learned once from the newest copy, then skipped when unchanged and replaced when modified.
  - An unreadable store reported while the others are learned, and a missing profile reported as absent.
  - Codex session and turn cwds as ownership evidence, one resolution per distinct cwd, the Stop-hook import of a rollout, and project naming.

  The full suite passed on Linux (271 tests).
- Go: `cmd/learn_test.go` covers local profiles from `CLAUDE_CONFIG_DIR`/`CODEX_HOME`, grouping and deduplication of WSL registrations, invalid and unreachable registrations, one mount-table read per distribution and user, each group's arguments and environment, and the launcher sharing the mount snippet. `go build ./...`, `go vet` and `go test ./...` passed on Linux, and the learn tests passed in a Windows test binary.
- Native Windows, on a machine with the two Ubuntu registrations, using a cross-compiled test binary and nothing written to the installation:
  - Discovery against the real `.wsl-registrations.json` returned one Ubuntu group with both stores and the drive mount table read through `wsl.exe`.
  - A registration for a missing distribution was reported with `wsl.exe`'s message.
  - The installed Windows interpreter, started with each group's environment, resolved the WSL stores to `\\wsl.localhost\Ubuntu\…` and `/mnt/c/…` to `C:\…`, while this computer's paths stayed unchanged.
- Native Windows, read-only, with the installed interpreter against the real WSL stores and a copy of the workspace policy: 146 conversations listed and identified with distinct sources. The ownership read of all 1.8 GB took 407 s with one resolution per distinct cwd; per-record resolution had not finished after 25 minutes. Details are in [platform validation](../platform-validation.md#learn-from-wsl-and-codex-stores-2026-10-24).
- Not run: a complete `imprint learn` that embeds and stores into a Windows data directory, the dashboard **Learn** action on Windows, and macOS.

Cursor follow-up: [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) adds this computer's Cursor profile and registered WSL Cursor profiles to the stores.
