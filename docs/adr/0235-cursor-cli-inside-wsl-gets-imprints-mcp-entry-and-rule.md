---
title: ADR 0235 — Cursor CLI inside WSL gets Imprint's MCP entry and rule
description: When Cursor CLI is on a WSL distribution's login PATH, registering Cursor there writes the bridge launcher, an imprint entry in the profile's ~/.cursor/mcp.json and Imprint's rule, as for the other clients that run inside WSL; without it the registration still only lets learn read Cursor's conversations.
---

- Status: Accepted
- Date: 2026-11-28
- Amends:
  - [ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) — its "Stores": a WSL Cursor registration writes the launcher, MCP entry and rule into the profile when Cursor CLI is in the distribution, which its rejected alternative "Write MCP configuration and hooks into the WSL `~/.cursor`" had ruled out
- Related: [ADR 0026](0026-windows-installer-wsl-client-bridge.md) (the WSL bridge), [ADR 0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md) (the `imprint` command in WSL homes), [ADR 0214](0214-the-rules-block-shrinks-to-a-stub-once-agents-keep-searching-first.md) (the short rules block), [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) (a client the Dispatcher starts must be connected to Imprint)

## Context

[ADR 0052](0052-learn-cursor-conversations-from-their-folder-names.md) made `cursor` a WSL registration target that writes nothing into the profile. Its reason: with Remote-WSL, the Windows Cursor app runs the agent, and its MCP server and hooks come from Imprint's Windows Cursor setup. It rejected writing MCP configuration and hooks into the distribution's `~/.cursor` because "Cursor does not run its agent from there".

Cursor CLI does. Cursor's installer puts it in the distribution as `~/.local/bin/agent` and `~/.local/bin/cursor-agent`, two links to one binary. `~/.local/bin` is on the login shell's PATH only. On the owner's device, Cursor CLI 2026.11.22 was installed in Ubuntu-26.04 on 2026-11-28. Its `agent mcp` help says it reads MCP servers from `.cursor/mcp.json` or `~/.cursor/mcp.json`. Inside the distribution that is the WSL home. That file did not exist, and `agent mcp list` reported no servers. So Cursor CLI there could not reach Imprint, and the Dispatcher's Cursor runner (Task T-gep6e) could never count it available: [ADR 0222](0222-only-clients-with-a-sandbox-or-a-check-of-each-action-are-dispatched.md) requires that "Imprint is connected to it".

Other evidence:

- **Rules.** Cursor CLI's bundle has a `HomeCursorRulesService` that loads `<home>/.cursor/rules/**/*.mdc`. On the owner's device, a probe rule there was applied from a git repository under the home folder, and not from a workspace in `/tmp`.
- **Hooks.** Cursor CLI loads hooks files. It also runs Claude Code's hooks: with Claude Code registered in the same distribution, a Cursor CLI session's context held the reminder that Imprint's Claude Code SessionStart hook prints.
- **The Cursor app over Remote-WSL.** Cursor's forum says the editor window reads the remote host's `~/.cursor/mcp.json`: "Editor Window delegates resolution to the remote extension host correctly, but in some cases Agents Window pulls state from the local Windows host" ([June 2026](https://forum.cursor.com/t/mcp-breaks-after-switching-between-cursor-ide-and-agents-window-on-the-same-wsl-remote-workspace/162587); a [WSL user's config](https://forum.cursor.com/t/looking-in-wrong-spot-to-discover-mcp-servers/159930) was read from the WSL home in May 2026; the [SSH case](https://forum.cursor.com/t/user-mcp-servers-in-agents-window-are-inconsistent-with-editor-window-when-using-remote-ssh/161173) of the Agents window was reported fixed in July 2026). No page says whether a window reads both files. The owner's device has no Cursor app, so this was not checked there.

## Decision

**Detection.** The WSL profile probe reports Cursor CLI: `command -v cursor-agent`, else `command -v agent`, in a login shell (`sh -lc`) with `HOME` set to the probed home. That is the PATH where the Dispatcher finds a client inside a distribution ([`imprint/dispatch/wsl.py`](../../imprint/dispatch/wsl.py), `which`). The command's path is recorded as `cursor_cli` in the profile and the registration journal.

**With Cursor CLI.** Registering Cursor for the profile writes into its `~/.cursor`, as for the other clients that run inside WSL:

- the `imprint-wsl` bridge launcher;
- an `imprint` entry, `{"command": "<launcher>", "args": ["mcp"], "env": {}}`, under `mcpServers` in `mcp.json`, beside any other servers;
- the rule `rules/imprint.mdc`, a file Imprint owns, holding Cursor's rule: the short block unless `instructions.full_rules` lists `cursor` ([ADR 0214](0214-the-rules-block-shrinks-to-a-stub-once-agents-keep-searching-first.md)).

It writes no hooks. The home gets the `imprint` command, as every registration does ([ADR 0091](0091-wsl-homes-with-a-connected-client-get-the-imprint-command.md)).

**Without Cursor CLI.** The registration journals the profile so learn reads its conversations, and writes nothing, as ADR 0052 decided.

**Removal.** `imprint wsl remove`, `imprint disconnect cursor`, disable and uninstall take out the entry, the launcher and the rule, when the rule is Imprint's full or short text. Other servers and rules stay. A profile without Imprint's launcher holds nothing a registration wrote into its registry. There, an `imprint` entry that points elsewhere, such as a Linux installation's, is left alone and removal succeeds. With the launcher present, such an entry is still refused, for every target.

The Windows Cursor setup is unchanged.

## Rejected alternatives

- **Keep Cursor's WSL registration learn-only (ADR 0052 as written).** Cursor CLI inside the distribution cannot reach Imprint, and the Dispatcher's Cursor runner can never be available on a device whose projects are in WSL.
- **Write the entry whenever the profile's `~/.cursor` exists, with or without Cursor CLI.** Repair and silent updates replay saved Cursor selections, so every existing learn-only registration would start writing into `~/.cursor`. Its only beneficiary would be the Cursor app over Remote-WSL, and which file that app reads is known only from forum posts and was not checked on a device.
- **Look for Cursor CLI at `~/.local/bin/agent` only, without a shell.** That misses other install locations and PATH changes the login shell makes. The Dispatcher already finds WSL clients through the login shell, and one rule keeps the two in step.
- **Detect `cursor-agent` alone.** It is the less ambiguous name, but the installer puts both names, and the Task names either. `cursor-agent` is checked first.

## Consequences

- **What works.** Cursor CLI sessions inside WSL reach the Windows runtime's memories and Board through the bridge. Cursor sets no Claude Code session id, so Board claims made from there hold as the server process.
- **Approval.** Cursor CLI asks the user to approve the `imprint` server on first use; `--approve-mcps` approves it for one run. In print mode (`-p`) an MCP tool call is rejected unless it is allowed, for example by `Mcp(imprint:search)` in a project's `.cursor/cli.json`, or unless `--force` is given. The Dispatcher's Cursor runner (T-gep6e) has to choose one of these.
- **Startup race.** An interactive session whose first prompt is passed on the command line can look for Imprint's tools before the bridge's server is up. Seen once on the owner's device: about 5 s after start, the session called Imprint "in an error state". Typed 25 s after start, the same request worked. Print mode waited for the server.
- **Where the rule applies.** It reaches workspaces under the home folder, where WSL projects and the Dispatcher's worktrees live. In a workspace outside the home, Cursor CLI 2026.11.22 did not apply it; the MCP entry still works there.
- **Existing installations.** A Cursor selection registered before this release gets the files at its next `imprint wsl setup`, repair or update, if Cursor CLI is in that distribution by then.
- **The Cursor app over Remote-WSL.** By Cursor's forum, each window reads one user-level `mcp.json`: the editor window the distribution's, the Agents window in some versions the Windows one. Imprint then appears once in each. Both entries are named `imprint`, so a window that merged the two files by server name would still list one server. A device check is still owed (Validation).
- **Hooks.** Cursor CLI gets no Cursor hooks in WSL. Where Claude Code is registered in the same distribution, Cursor CLI runs its hooks, including Imprint's. Which hooks Cursor CLI should get, and how to avoid running both sets, is left to Task T-qhy1a.
- **Probe cost.** Each profile probe now starts a login shell. Through `wsl.exe` the whole probe took 0.16 s on the owner's device. A slow `~/.profile` slows `imprint wsl list` and setup, within the existing 30 s limit of each WSL command.
- **Removal leaves less behind.** A foreign `imprint` entry in a profile where Imprint's launcher is absent no longer fails a disconnect. The registration's record is dropped and the foreign entry stays.

## Invalidation conditions

- If Cursor CLI moves its user configuration out of `~/.cursor/mcp.json`, for example to a directory an environment variable names, the layout and the probe follow it.
- If a device shows the Cursor app over Remote-WSL reading the distribution's `~/.cursor/mcp.json` and not the Windows one, the registration should write the entry whenever the profile exists, with or without Cursor CLI. That ends ADR 0052's learn-only registration.
- If a device shows the Cursor app listing Imprint twice with this entry, the registration must not write the WSL entry while the Windows Cursor setup is present.
- If a program named `agent` that is not Cursor CLI turns up on WSL login PATHs, detection must check the binary itself (its `--version` or the link's target) before writing.
- If Imprint wires hooks for Cursor CLI in WSL (Task T-qhy1a), that decision amends this one.

## Validation

- **Go**, on Linux with go1.27.4: `go build ./...`, `go vet ./cmd/` and `go test ./...` passed.
  - `TestWSLCursorCLIRegistrationWritesItsMCPEntryAndRule`: the probe's login-shell line, and a fixture profile with Cursor CLI. Setup writes the launcher, the entry beside another server, and the short rule beside a user rule. A second setup leaves all three byte-identical. `disconnectClients(cursor)` removes the entry, launcher, rule and record, and keeps the other server, the file's other keys and the user rule.
  - `TestWSLCursorRegistrationWithoutCursorCLIRecordsItsStoreAndWritesNothing`: without Cursor CLI nothing is written, and disable keeps a Linux installation's `imprint` entry unchanged.
  - Removing each new rule (the idle layout, the short-rule match on removal, the foreign entry without a launcher) makes one of these tests fail.
- **Owner's device** (Windows with WSL Ubuntu-26.04, Cursor CLI 2026.11.22, installed runtime v0.64.0-dev.1). A Windows build of this change was placed beside the installed `imprint.exe`, so it used the installed runtime and data, and was removed afterwards.
  - The probe through `wsl.exe` reported `/home/alexa/.local/bin/cursor-agent`; with a home that has no Cursor CLI it printed an empty line.
  - `wsl setup --distribution Ubuntu-26.04 --target cursor` wrote the launcher (mode 700), `mcp.json` and the rule, and journaled `cursor_cli`. The launcher's `probe` ran the Windows interpreter.
  - `agent mcp list` listed `imprint: ready`, and `agent mcp list-tools imprint` listed its 23 tools.
  - `agent -p --approve-mcps --trust` called `search` and returned a stored memory's id. A first try without allowing the tool was rejected ("User rejected MCP: imprint-search").
  - An interactive `agent` session in a pseudo-terminal, with the request typed 25 s after start, called `search` and answered with the same id.
  - A probe rule in `~/.cursor/rules` was applied from a git repository under the home folder, with Imprint's rule in context, and not from `/tmp`.
  - `disconnect cursor` left `mcpServers` empty and removed the launcher, the rule and the journal record. The Claude Code and Codex registrations stayed. Afterwards the home's `imprint` command, the journal and the Claude Code launcher matched their checksums from before the run.
- **Not run:**
  - the Cursor app over Remote-WSL: the owner's device has no Cursor app, so whether it shows Imprint twice is unchecked;
  - the installer wizard, repair or an update with a Cursor selection;
  - a cross-compiled Windows test binary of the Go tests.
