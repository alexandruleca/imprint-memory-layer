---
title: ADR 0072 — Projects name their workspace and each device approves it
description: A project's .imprint/workspace.json proposes its workspace and this device's approved roots decide; one resolution serves MCP sessions, hooks, CLI ingestion and learning, sessions ask once, and use_workspace assigns a project without a restart.
---

- Status: Accepted
- Date: 2026-10-26
- Amends:
  - [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) — each MCP session binds to its project's workspace, the per-project binding 0006 left open
  - [ADR 0015](0015-workspace-roots-and-source-purge.md) — the MCP startup pin follows the session's directory instead of the global selection, `ingest` and `refresh` use their target directory's workspace, and a root also approves the project files inside it
  - [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md) — a project file this device approved assigns its project's conversations to the one workspace it names, and the Stop hook learns in the session's workspace
- Related: [ADR 0016](0016-sync-direction-and-workspace-sharing.md), [ADR 0026](0026-windows-installer-wsl-client-bridge.md), [ADR 0045](0045-setting-choices-and-gpu-auto-select.md), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md), [workspaces](../workspaces.md)

## Context

The owner asked for "a mechanism to ask the user which workspace he wants to have as default for this directory that he opens in ide or cli etc, he should also be able to create a new workspace, and that workspace should be saved locally in a file so imprint can read it and know to switch workspaces; this also works for desktop app conversations". The owner decided that the choice is a file in the project and that git treatment is left to the user.

Before this record, every MCP server process pinned the global active workspace (`data/workspace.json`) when it started (`imprint/__main__.py`, ADR 0015). A session could follow a project only through `IMPRINT_WORKSPACE` in the client's server environment, which is one value per client registration, or through each tool's explicit `workspace` argument. ADR 0006 recorded the gap: "This is not yet persistent per-project/client workspace binding." The CLI pinned the active workspace too (`pinWorkspace`), and learning gave unclaimed conversations to whichever workspace ran it (ADR 0034).

What a server can know about its session depends on the client (research for this record, from vendor docs and client source; see Validation for what was not verified):

| Client | Working directory of a stdio server | Server processes |
| --- | --- | --- |
| Claude Code (CLI and IDE extensions) | the session's project folder (observed); `CLAUDE_PROJECT_DIR` is set | one per session |
| Codex CLI and IDE extension | the session's folder, unless the config sets `cwd` (source) | one per session |
| VS Code with GitHub Copilot | "Defaults to the workspace folder when run in a workspace" (docs) | per window |
| GitHub Copilot CLI | undocumented; presumably its working directory | per session |
| Cursor | undocumented; one report says it is not the project | undocumented |
| Cline | the VS Code extension host's directory unless the config sets `cwd` (source) | one per extension, shared by tasks |
| Claude Desktop | "may be undefined (like `/` on macOS)" (MCP debugging guide) | one per app launch, shared by every chat |
| ChatGPT Desktop | registered through the Codex configuration it shares (docs); its Codex threads presumably run like Codex | presumably per thread |

On Windows, an app installed from MSIX starts with `C:\Windows\System32` as its working directory by default (MSIX packaging guidance; Claude Desktop installs that way on the owner's machine), and many apps install under the user's home (`%LOCALAPPDATA%`). MCP's `roots/list`, the protocol's way to ask a client for its folders, is deprecated by SEP-2577 in the 2026-07-28 specification, and Codex, Cline and the Copilot CLI do not implement it.

A project file travels with the repository. A cloned repository, including an untrusted one, could name any workspace, for example a client's, and a session that obeyed it would store and learn that repository's conversations there.

## Decision

**The project file.** `.imprint/workspace.json` holds `{"workspace": "<name>"}`, validated with the workspace naming rules. A directory's file is the nearest one found walking up from it. The walk visits the directory and its ancestors and stops before a filesystem root, a home directory or an ancestor of one: a file there would assign every directory beneath it. The homes are the runtime user's and, for a WSL client of a Windows runtime, the client's (the bridge passes it as `IMPRINT_WSL_HOME`). A file that cannot be read, is not that object or names an invalid workspace is ignored, and the note saying so reaches the user (CLI output, session notice).

**The file proposes; this device decides.** A file's workspace is honoured only when a root of that workspace contains the file's directory. Approved roots (ADR 0015) are the device-local trust record: one approval both admits a directory to ingestion and accepts the project files inside it. An unapproved proposal assigns nothing; the session asks the user about it.

**One resolution** (`workspace_policy.resolve`) answers which workspace a command or session in a directory uses. In order: a tool's explicit `workspace` argument (per call, unchanged), `IMPRINT_WORKSPACE`, the directory's approved project file, the workspace whose root containing the directory is most specific (roots of several workspaces at the same depth leave it unassigned), then a default. The default is the active workspace for a CLI command and the `workspace.unassigned` setting for an agent session; that setting's empty default follows the active workspace, and Settings offers this device's workspaces as its choices (ADR 0045).

**A session's project.** A session has a project when its working directory is in one: the nearest directory, within the same walk, that holds a project file or is a git repository's root. A working directory that is neither in a git repository nor under a project file has no project, whatever it is: the filesystem root, a home directory, an application's own folder, or a plain folder.

**MCP sessions.** At startup the server resolves its working directory (the WSL bridge sets it to the client's) and pins the result in `IMPRINT_WORKSPACE`, as before, so another window's switch cannot redirect it. When the session's project has no workspace, or its file proposes one this device has not approved, the first tool result of the session starts with a notice: which workspace the session uses meanwhile, the workspaces that exist, and the request to ask the user once and call `use_workspace`. Nothing blocks; later results carry no notice. A session without a project is never asked. `device_context` reports the session's workspace and why.

`use_workspace(name, allow_overlap=False)` acts on the user's choice, including confirming a proposal:

- In a project it creates the workspace if new (`config.register_workspace`, as the CLI and dashboard do), approves the project folder as a root unless a root of that workspace already contains it, writes `.imprint/workspace.json` in the project folder, and re-pins this session. When other workspaces' roots overlap the folder, nothing changes until the call is repeated with `allow_overlap` after the user confirms the sharing, as for any root (ADR 0015). The answer says the file was written and that committing it is the user's choice.
- Without a project, for example a desktop app whose one server serves every chat, it creates the workspace if new and switches nothing: the agent passes `workspace` on every later call in that conversation. The server instructions say the same, including for a line such as "Use Imprint workspace `<name>`" in a Claude or ChatGPT Project's instructions.

**Hooks.** The session-start hook (Claude Code, Codex and GitHub Copilot send the session's `cwd`) adds the session's workspace and why, or the same notice, to the reminder; without a `cwd` (Cursor) it says nothing about workspaces. The Stop hook, native and through the WSL bridge, runs its import, extraction and summary in the workspace an MCP session in the payload's `cwd` uses.

**CLI.** `imprint workspace use [<name>] [--allow-overlap]` does what `use_workspace` does for the project around the current directory, or for the current directory when it is in none, since a person chose to run it there; it refuses a home directory or a filesystem root. Without a name it lists the workspaces and the proposal and asks, but only on a terminal; otherwise it fails with its usage. `imprint ingest` and `imprint refresh` resolve the workspace of the directory they act on (a file's folder) and print it and why. `learn`, `move`, `purge` and the roots commands keep the active workspace or `--workspace`; `imprint workspace switch` still sets the global selection only.

**Learning.** `transcript_allowed` keeps one rule with a wider notion of claim. A recorded cwd is claimed by the workspace its approved project file names, otherwise by every workspace whose roots contain it; a conversation belongs to a workspace when every cwd is claimed by it or by none. Without a project file this is ADR 0034's rule unchanged, so confirmed shared roots still share their conversations.

**Git** is never touched: no `.gitignore` or `.git/info/exclude` edits.

## Rejected alternatives

- **The cwd as the project when it is not in a git repository** (the requested "else the cwd"). MSIX apps start with System32 as their working directory, Windows apps install under the home directory, and Cline and possibly Cursor give a server their host's directory. Such sessions would be asked about, and would write a project file into, an application's folder. Requiring a project file or a git repository needs no list of application folders. The CLI keeps the fallback because a person ran it in that folder.
- **MCP `roots/list` for the session's folder.** Deprecated by SEP-2577 in the 2026-07-28 specification, and Codex, Cline and the Copilot CLI do not answer it.
- **Recognising desktop apps by MCP `clientInfo` names or a flag in their registration.** Names are a list to keep current, and a registration flag covers only registrations Imprint wrote. With projects defined by evidence, Claude Desktop's undefined or System32 working directory has no project without either.
- **Trusting the project file alone.** A cloned repository could send a session's memories and conversations into any of the user's workspaces.
- **A separate trust list for project files.** Roots already record this device's approval of a directory and gate its ingestion; two records could disagree about the same folder.
- **Switching the server's workspace in `use_workspace` without a project.** A desktop app's server is shared by every open chat, which would all move silently.
- **A default for desktop apps only, with the active workspace for unassigned projects.** Plain folders in coding clients have no project either, so "apps" would not describe what the setting covers; one default for every session nothing assigns is one rule. Unset, it is the active workspace, so nothing changes for users who leave it.
- **Most specific root for learning too.** It would stop nested, confirmed shared roots from sharing their conversations (ADR 0034).
- **Resolving `ingest` and `refresh` from the shell's working directory.** These commands name the directory they act on.
- **Resolving `learn` from the shell's working directory.** Each conversation's own directory already decides its ownership; the learning workspace would otherwise depend on where the command was typed.
- **Blocking tools until the user answers, or asking in every result.** The session keeps working in its default and asks once.
- **Adding the file to `.gitignore` or `.git/info/exclude`.** The owner left git to the user.

## Consequences

- Sessions in an assigned project use its workspace from their first call, and `use_workspace` assigns a project without restarting the client. Sessions nothing assigns behave as before unless `workspace.unassigned` is set.
- The Stop hook captures a project's conversations into the project's workspace. With a project file, the global active workspace would refuse them under the extended rule, so the hook must resolve the session's workspace.
- A cloned project with a file changes nothing until the user confirms; confirming approves the project folder as a root of that workspace.
- Clients with a session-start hook and MCP may show the question twice, from the hook and from the first tool result. Two processes cannot tell that they serve one session, so both say "ask once".
- Cursor's hook payload carries no `cwd`, and its servers' working directory is undocumented; Cline's servers run in the extension host's directory. Their sessions often have no project, so they use `workspace.unassigned` and per-call `workspace` until the user assigns the folder with the CLI.
- Folders outside git repositories become projects only through `imprint workspace use`.
- Each learned conversation's cwds are looked up for a project file, one filesystem check per directory level up to the home directory, through `\\wsl.localhost` for WSL stores. This was not measured.
- Deleting a workspace resets `workspace.unassigned` when it names that workspace.
- A session resolves once, at startup. A project file another terminal writes reaches sessions started afterwards, or this one through `use_workspace`.
- The dashboard is unchanged: its jobs carry its selected workspace in `IMPRINT_WORKSPACE`, which outranks directories.

## Invalidation conditions

- If MCP gains a supported way for a server to learn each request's folder or conversation, resolve per request instead of per process, and let desktop apps switch per chat.
- If Cursor or Cline is shown to start servers in the project folder, their sessions gain projects without change. If a widely used client starts servers inside an application folder that is a git repository, requiring a project file or a git repository no longer separates projects from applications, and the project rule must be revisited.
- If project-file lookups make learning measurably slower (for example, learn time over `\\wsl.localhost` more than doubles), cache them for the run.
- If project files must carry more than the workspace name, such as a project name or sharing intent, revisit the format and the trust rule.
- If users report being asked twice, give the hook and the server a shared marker for the session.

## Validation

- Python, Linux: `tests/test_workspace_directories.py` covers the precedence order, tied roots, the nearest-file walk and its home, WSL-home and filesystem-root boundaries, invalid files, untrusted proposals and their confirmation, sharing confirmation, root reuse, CLI `use` at a git root and in a plain folder (and refusing home), learning with approved, unapproved and shared files, and resetting the setting when a workspace is deleted. In-memory FastMCP sessions cover the single notice, `use_workspace` switching without a restart, overlap confirmation, sessions without a project (none, `/`, home, a plain folder) using `workspace.unassigned` and never switching, and an unresolvable directory. Hook tests cover session-start for Claude Code and Copilot shapes and the Stop hook's workspace; `tests/test_wsl_bridge.py` covers the bridge's Stop check in the session's workspace.
- Go, Linux: `cmd/workspace_test.go` covers `workspace use` argument parsing (no name without a terminal is a usage error) and the picker; the CLI builds and vets for Linux, Windows and macOS.
- End to end on Linux (WSL), with a temporary data and home directory: `imprint workspace use` named, through the picker on a pseudo-terminal, from `/dev/null` and a pipe (refused), in a home directory (refused) and against another workspace's root; `imprint ingest` and `imprint refresh` printing their workspace from a project file, an unapproved proposal and `IMPRINT_WORKSPACE`; three real stdio sessions of `python -m imprint` (an unassigned git project asked once, then `use_workspace` and a new session resolving the file; a session in `/` creating a workspace without switching).
- Dashboard: TypeScript, ESLint, production build and Playwright, including the Settings choice for `workspace.unassigned`.
- Not verified: native Windows and macOS runs, the WSL bridge against a Windows runtime, and every real client. The table in Context comes from vendor documentation and client source, not from running Cursor, Cline, VS Code, the Copilot CLI, Codex, Claude Desktop or ChatGPT Desktop against this change. Claude Desktop's working directory on Windows and ChatGPT Desktop's handling of stdio servers outside Codex threads are unconfirmed.
