---
title: ADR 0236 — Cursor gets Imprint's hooks wherever it runs, and one hook answers each event
description: Cursor's hooks.json gets Imprint's sessionStart, preCompact, postToolUse, stop and sessionEnd hooks on Windows, macOS, Linux and, with Cursor CLI, inside WSL; on Windows the commands use PowerShell's call operator; and because Cursor also runs Claude Code's hooks, the first of Imprint's hooks to claim a Cursor event answers it.
---

- Status: Accepted
- Date: 2026-11-28
- Amends:
  - [ADR 0235](0235-cursor-cli-inside-wsl-gets-imprints-mcp-entry-and-rule.md) — hooks: a WSL Cursor registration with Cursor CLI writes Imprint's Cursor hooks into the profile's `~/.cursor/hooks.json` through the launcher
- Related: [ADR 0026](0026-windows-installer-wsl-client-bridge.md) (the WSL bridge), [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) (Cursor's conversations), [ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md) (PowerShell hook commands for Copilot), [ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md) (the detached learner), [ADR 0142](0142-agents-are-reminded-to-search-imprint-never-refused.md) (the read reminder)

## Context

Imprint's Cursor setup ([`cmd/setup_cursor.go`](../../cmd/setup_cursor.go)) writes `sessionStart`, `preCompact` and two `postToolUse` hooks into `~/.cursor/hooks.json` on the computer Imprint runs on. [ADR 0235](0235-cursor-cli-inside-wsl-gets-imprints-mcp-entry-and-rule.md) connected Cursor CLI inside WSL without hooks and left them to Task T-qhy1a. The owner then asked that Cursor CLI work on macOS and on Windows itself as well.

**What Cursor CLI does with hooks.** This was checked on the owner's device on 2026-11-28 with Cursor CLI 2026.11.22 in WSL Ubuntu-26.04. The check used a scratch home whose `~/.claude/settings.json` and `~/.cursor/hooks.json` held capture hooks for every event, with the sign-in borrowed through `XDG_CONFIG_HOME`. It made one print run (`-p`) and one interactive run in a pseudo-terminal, and read the CLI's bundle.

- **Cursor runs Claude Code's hooks.** Its hooks loader reads `~/.claude/settings.json` and a project's `.claude/settings.json` and `settings.local.json`. This is Cursor's "third-party extensibility", on by default. It maps Claude Code's events to its own:
  - SessionStart→sessionStart, PreToolUse→preToolUse, PostToolUse→postToolUse, Stop→stop;
  - PreCompact→preCompact, UserPromptSubmit→beforeSubmitPrompt, SessionEnd→sessionEnd.

  Matchers are rewritten: `mcp__imprint__search` becomes `MCP:search`, and Glob is dropped. `async` is dropped, and SessionStart's `startup|resume` is ignored. The converted hooks are fail-open (`failClosed: false`). So where Claude Code and Cursor are both connected, a Cursor session ran Imprint's hooks from both files.
- **Cursor drops exact duplicates.** A Claude Code hook is not run when its command text equals that of a Cursor hook for the same event; the bundle logs "Removed duplicate claude-user hook". The comparison is the command text alone. In the probe, one command placed in both files ran once.
- **Payloads.** Claude Code's hooks get Cursor's payload unchanged. It carries `session_id` (equal to `conversation_id`), `generation_id`, a camelCase `hook_event_name`, `cursor_version`, `workspace_roots` and `transcript_path`, and no `cwd`.
  - Hooks run with their configuration folder (`~/.cursor`, `~/.claude`) as working directory, so the project appears only in `workspace_roots`.
  - `transcript_path` is null at sessionStart. From the first model turn on it names `~/.cursor/projects/<folder>/agent-transcripts/<id>/<id>.jsonl`, which `imprint learn` already reads ([ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md)).
- **Events.** A print run fires sessionStart, the tool hooks and sessionEnd, and never stop. An interactive session also fires beforeSubmitPrompt, and stop after each turn.
- **Outputs.** Both Cursor's `additional_context` and Claude Code's `hookSpecificOutput.additionalContext` reached the model from sessionStart, preToolUse and postToolUse.

So today, in a WSL profile where Claude Code is registered, a Cursor CLI session runs Imprint's Claude Code hooks, through Claude Code's launcher:

- session-start, which gives the reminder without a workspace, because there is no `cwd`;
- pre-read on Read and Grep, which is silent for Cursor's payloads;
- post-search;
- stop on Cursor's transcript;
- pre-compact, which answers with Claude Code's `systemMessage`.

**Windows.** Cursor builds each hook into a PowerShell script: `$OutputEncoding = …UTF8; Get-Content -LiteralPath '<payload file>' -Raw | & { $input | <command> }`. It falls back to bash only when started from Git Bash, a bug that breaks every hook. Cursor staff confirmed both, along with a UTF-8 byte order mark before the payload, between August and October 2026 ([168129](https://forum.cursor.com/t/cursor-agent-windows-imported-claude-code-hooks-are-composed-as-powershell-but-executed-with-bash-silently-blocking-every-tool-call/168129), [172789](https://forum.cursor.com/t/cursor-agent-on-windows-launched-from-git-bash-rejects-every-hook-the-powershell-wrapper-is-run-with-bash-eval/172789), [168407](https://forum.cursor.com/t/hooks-not-firing-cannot-have-guardrails/168407)).

On the owner's Windows PowerShell 5.1 that wrapper behaved as follows:
- The payload arrived after the byte order mark (`EF BB BF`).
- Imprint's Windows Cursor command, `"python.exe" "hook_entry.py" action "data"`, failed to parse.

So Imprint's Cursor hooks have not run on Windows. The device has no Cursor for Windows.

## Decision

**The hooks.** Wherever Imprint connects Cursor, `hooks.json` gets the same six entries, beside the user's own:
- on Windows, macOS and Linux: `~/.cursor`, which the Cursor app and Cursor CLI both read and which Cursor CLI creates;
- inside WSL: a profile whose login shell finds Cursor CLI ([ADR 0235](0235-cursor-cli-inside-wsl-gets-imprints-mcp-entry-and-rule.md)).

| Event | Matcher | Action |
| --- | --- | --- |
| sessionStart | | `session-start` |
| preCompact | | `pre-compact` |
| postToolUse | `MCP:search` | `post-search` |
| postToolUse | `Read\|Grep` | `cursor-post-read` ([ADR 0142](0142-agents-are-reminded-to-search-imprint-never-refused.md)) |
| stop | | `stop` |
| sessionEnd | | `stop` |

stop and sessionEnd start the detached learner on the transcript Cursor names ([ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md)). sessionEnd is what learns a print run.

In WSL each command is the profile launcher's: `'<home>/.cursor/imprint-wsl' hook <action>`. The bridge accepts `cursor-post-read` and translates `workspace_roots` the way it translates `cwd`. Removal takes out the entries that name the launcher and leaves the rest of the file.

**Commands.** Outside Windows, each command is byte for byte the one Claude Code gets for the same action, so Cursor drops Claude Code's copy. On Windows each command uses PowerShell's call operator: `& '<python>' '<hook_entry.py>' <action> '<data>'`. That is the form Copilot's hooks already use ([ADR 0057](0057-copilot-home-as-store-config-and-hook-root.md)).

**One hook per event.** When the payload is Cursor's (it carries `cursor_version`), the `session-start`, `pre-compact` and `stop` actions first claim the event:
- The claim is a file under the data folder's `.sessions/once`, named by the hash of the session, event, generation and current minute, and created exclusively.
- The hook that creates the claim acts. A hook that finds it already there prints `{}` and stops.
- Claims older than a day are removed at the next sessionStart.

This covers the cases Cursor's duplicate rule cannot:
- WSL, where Claude Code's and Cursor's launchers differ;
- Windows, where Claude Code's hooks run in Git Bash and Cursor's in PowerShell, so the commands differ.

`post-search` only touches a sentinel and `pre-read` is silent for Cursor's payloads, so neither claims.

**Cursor's payloads.** The first workspace root stands for the session's directory. The session-start context then names the session's workspace and its project's Board, and the Stop hook learns in that workspace. The hook reads its payload past any UTF-8 byte order marks. `pre-compact` answers with `user_message` for a Cursor payload and `systemMessage` otherwise. The earlier `cursor-pre-compact` action keeps working for hooks files an earlier release wrote, until setup rewrites them.

## Rejected alternatives

- **Write no Cursor hooks in WSL and rely on Claude Code's, which Cursor CLI runs.** This works only where Claude Code is registered in the same profile and the user leaves third-party extensibility on. A profile with Cursor alone would get no reminder and no learning.
- **Make Imprint's Claude Code hooks do nothing for a Cursor payload.** A Cursor session would then lose everything wherever Imprint's Cursor hooks are missing or do not run. Windows was such a case until this change. The hook also cannot tell whether Cursor's own hooks are installed: in WSL that would mean reading the profile's `hooks.json` from Windows on every hook.
- **Rely on Cursor's duplicate rule alone.** The probe showed that it compares command text only. WSL launchers differ per client (`~/.claude/imprint-wsl` and `~/.cursor/imprint-wsl`). On Windows, PowerShell's call operator is a syntax error in the Git Bash that Claude Code runs hooks in, so the two commands cannot be the same.
- **Point Cursor's WSL hooks at Claude Code's launcher, so the texts match.** That ties Cursor's registration to Claude Code's: disconnecting Claude Code removes the launcher Cursor's hooks run.
- **Claim by session and event without the minute.** At sessionStart the generation id equals the conversation id. A resumed session would therefore claim the same name again and get no reminder.
- **Keep the double-quoted command on Windows.** It fails to parse in Cursor's PowerShell wrapper (Context).

## Consequences

- **What Cursor sessions get.** On every platform, Cursor sessions now get what Claude Code's get:
  - the session-start reminder with the workspace and the Board;
  - the read reminder and the search sentinel;
  - the compaction nudge;
  - their conversation learned when a turn or a session ends.

  In WSL a Cursor profile no longer depends on a Claude Code registration beside it.
- **Windows.** Imprint's Cursor hooks run there for the first time. The Claude Code hooks that Cursor also loads keep their Git Bash form and still fail to parse in Cursor's PowerShell wrapper. Cursor marks converted hooks fail-open, so they cost a failed PowerShell start per event and refuse nothing. Started from Git Bash, Cursor on Windows breaks every hook, Imprint's included (its forum, above).
- **Two processes where both clients' hooks run.** In WSL and on Windows, both clients' hooks start for one event; the second exits after the claim. A claim is a file in the data folder, created once per Cursor event.
- **Existing installations.** At the next setup, repair or update, setup replaces Imprint's `cursor-pre-compact` entry (same event and matcher) and adds stop and sessionEnd. A WSL Cursor profile gets its hooks at its next `imprint wsl setup`, repair or update.
- **Learning.** Cursor's conversations are learned as soon as a turn ends, as the same sources `imprint learn` reads. The learner coalesces a burst of turns into one run ([ADR 0077](0077-stop-hook-hands-turns-to-one-waiting-learner.md)).
- **The Board.** Cursor's conversation id is not the MCP server's Board session: Cursor gives the server none ([ADR 0235](0235-cursor-cli-inside-wsl-gets-imprints-mcp-entry-and-rule.md)). So the session-start Board lines cannot name the session or count its unread comments.

## Invalidation conditions

- If Cursor stops running Claude Code's hooks, the claims become redundant, though harmless.
- If Cursor starts comparing duplicates by more than command text, the claims still hold one hook per event. On macOS and Linux both processes then start, as they do on Windows and in WSL.
- If Cursor's payloads gain a `cwd`, it is used before the workspace roots.
- If Cursor's payloads lose `workspace_roots`, the session-start context loses its workspace again.
- If Cursor's payloads stop carrying `cursor_version`, Cursor's payloads must be told apart another way (a camelCase `hook_event_name`), or the claims and the `user_message` answer stop applying.
- If Cursor on Windows runs hook commands in a shell other than PowerShell, the Windows commands follow that shell.
- If two of Imprint's hooks for one event can start more than a minute apart, for example behind a slow bridge, both act. The claim would then need an age check in place of the minute in its name.
- If a resumed Cursor session turns out to fire sessionStart with a new generation id, the minute can leave the claim's name.

## Validation

- **Device, native Linux** (the owner's WSL Ubuntu-26.04 as a Linux computer, standing in for macOS's path; Cursor CLI 2026.11.22). This change's `SetupCursor` and Claude Code's `writeClaudeCodeHooks` wrote their hooks into a scratch home, pointing at this tree's `hook_entry.py` through a logging interpreter, with a scratch data folder.
  - Print run (an Imprint search and a Read):
    - session-start, post-search, pre-read, cursor-post-read and stop (from sessionEnd) each ran once; Claude Code's copies of the same commands did not run.
    - The model's context held one reminder, naming the scratch folder's workspace.
    - One learner started on Cursor's transcript.
  - Interactive run (one turn, then `/compact`): session-start and pre-compact ran once each, and stop ran three times, for two turns and sessionEnd.
- **Device, WSL.** `applyWSLProfile` wrote Claude Code's and Cursor's registrations into a scratch home. Their launchers pointed at a Windows copy of this tree's `imprint` package and a scratch Windows data folder.
  - Print run: session-start ran through both launchers, and the model's context held one reminder, naming the workspace of the folder from its translated root. One learner started.
  - Interactive run: sessionStart, two stops and preCompact ran through both launchers, and sessionEnd through Cursor's alone. Each of the five events left exactly one claim.
  - The detached learners the runs started on the scratch data were stopped afterwards.
- **Windows** (no Cursor). `TestCursorHookCommandsExecuteInCursorsShell` was cross-compiled and run on the host with the installed venv's Python. It runs each Cursor command inside Cursor's PowerShell wrapper, byte order mark included, from an install path with spaces and an apostrophe, and passed. On the CI's Windows runner (a VM on bedrock), the same wrapper put two byte order marks before the payload, so the hook skips any number of them. PowerShell 5.1 showed the byte order mark and the earlier command's parse error.
- **Go** 1.27.4 on Linux: `go vet ./cmd/ ./internal/...` and `go test ./...` passed. The tests covered:
  - `TestCursorSetupWritesItsHooksBesideTheUsersOwn`: the six entries beside a user's hook, the earlier `cursor-pre-compact` and preToolUse entries replaced, a second setup byte-identical, and each command equal to Claude Code's outside Windows;
  - `TestWSLCursorCLIRegistrationWritesItsMCPEntryRuleAndHooks`: writing, re-running and removing the WSL hooks beside a user's hook;
  - `TestCursorHookCommandsExecuteInCursorsShell`.
- **Python**: `tests/test_hooks.py` (Cursor's root as the directory, one answer per event, one learner per transcript, one or two byte order marks) and `tests/test_wsl_bridge.py` (Cursor's hook and roots through the bridge).
- **Not run:**
  - the Cursor app on any platform;
  - Cursor CLI on macOS and on Windows;
  - PowerShell 7;
  - `imprint update` or repair rewriting an earlier `hooks.json` on a device;
  - whether a resumed Cursor session keeps its generation id at sessionStart.
