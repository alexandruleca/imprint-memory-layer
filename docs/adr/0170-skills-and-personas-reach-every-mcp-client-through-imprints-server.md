---
title: ADR 0170 — Skills and personas reach every MCP client through Imprint's server
description: The MCP handshake lists the active skills and personas a client does not already load from its own folders, a read-only skills tool lists and reads them, and a skill_edit tool creates and changes them, so desktop apps and WSL sessions get them too.
---

- Status: Accepted
- Date: 2026-11-10
- Amends: [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) — items also reach clients through Imprint's MCP server, not only through the clients' folders
- Related: [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (the registry), [ADR 0171](0171-personas-are-written-for-codex-and-cursor.md) (persona folders), [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md) and [ADR 0143](0143-agents-receive-generalised-guidelines-core-memories-are-kept-as-records.md) (guidelines in the handshake), [ADR 0026](0026-windows-installer-wsl-client-bridge.md) (the WSL bridge), [ADR 0117](0117-the-dashboard-shows-each-agent-session-and-its-calls.md) (the client's handshake name), [skills](../skills.md), [MCP tools](../mcp.md)

## Context

[ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) reaches a client only by writing files into folders it reads. On 2026-11-10 an agent in Claude's desktop app, connected to the Windows installation on the user's computer, was asked for the user's skills and reported that Imprint "doesn't expose a skills registry": its 19 tools were all memory tools, it published no resources, and a memory search found nothing about skills. That is the designed behavior, and it leaves out:

- Claude Desktop, ChatGPT Desktop, Cline and OpenClaw, which read no skill folders ([skills](../skills.md) listed them as getting none). This installation's Agents page records sessions from `claude-ai` and `local-agent-mode-imprint` beside `claude-code`.
- WSL sessions served by a Windows installation through the bridge ([ADR 0026](0026-windows-installer-wsl-client-bridge.md)): only the Windows home's folders are written.
- Personas everywhere but Claude Code.

The user asked for skills and personas to be discoverable by any agent using Imprint, and for the tool to create and edit them as well as read them.

## Decision

**Listed in the handshake.** The MCP server's instructions end, after the user's guidelines, with a block that names each active skill and persona with its description, and says to load one with `skills(action="get", name=...)` when its description fits the task. Descriptions are cut to 250 characters and the block to 1,500 estimated tokens (4 characters each). Items beyond that are listed by name, then counted with a pointer to `skills(action="list")`. Without items, or without a registry, there is no block, and reading it never creates the registry.

**Fitted to the client.** A client that loads the items from its own folders on this computer is not given them twice. The handshake names the client: `clientInfo` in `initialize`, or the `io.modelcontextprotocol/clientInfo` key of `server/discover`'s `_meta` on protocol 2026-07-28. A kind of item is left out when all three of these hold:

- The name is one whose client reads the folders Imprint writes: `claude-code`, `codex-mcp-client`, `cursor-vscode` or `opencode`.
- The last write into the clients' folders reported that client as a reader of a folder of that kind.
- The session is not served to WSL through the bridge (`IMPRINT_WSL_DISTRO` unset).

Any other client, and one that names itself in neither exchange, gets the whole list.

**Two tools.** `skills` is read-only: `list` gives every item that is not removed with its description, and `get` gives an item's `SKILL.md` or `PERSONA.md`, or another of a skill's files with `path`, 20,000 characters at a time with `next_start`. `skill_edit` writes:

- `create` keeps a new item from a name, a description and instructions, or from its files.
- `edit` replaces the description or the instructions, keeping the rest of the front matter as written. It adds or replaces files and removes one mapped to null, or sets `state` to `disabled` or `active`.

Both go through the registry ([ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md)): the same validation, credentials refused, and the replaced version kept in this device's history. Each change is written into the clients' folders at once. When the clients cannot be listed, the change is kept and the dashboard's next pass writes it. Removing an item stays with the user, in the dashboard or `imprint skills remove`.

**In the dashboard's chat and Echo.** They offer `skills` among their read tools, so they can follow a skill the user names. `skill_edit` stays with MCP clients, as `kg_edit` does.

## Rejected alternatives

- **MCP prompts.** [ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md) already rejected them, and its reason stands: a prompt runs only when the user invokes it, while the agent should load a skill on its own when the description matches.
- **One tool for reads and writes.** The user asked for "the tool" to create and edit too. Clients ask before running a tool that is not marked read-only, so one tool would make every listing ask, or make a write run unasked once a listing was allowed. Separate read and write tools follow `kg_query` and `kg_edit`.
- **The same list for every client.** Simpler, but Claude Code on this computer already lists the items from `~/.claude/skills` and `~/.claude/agents`, and the block would cost each of its sessions up to 1,500 more tokens for nothing.
- **Names without descriptions.** Cheaper, but agents choose a skill by its description, so a list of names would not lead them to load the right one.
- **Writing the WSL profiles' folders** ([ADR 0169](0169-skills-and-personas-are-written-into-the-folders-clients-read.md)'s invalidation condition). That needs the distribution running and reaches only clients that read folders. The server reaches every WSL session it serves without touching the profile.
- **Removing through `skill_edit`.** Removing an item takes it out of every client on every paired device. An agent can disable one, which `active` undoes, and the user removes.

## Consequences

- Any MCP client, including the desktop apps and WSL sessions, sees the user's skills and personas and can load them. In a client that cannot run subagents, a persona's prompt is a role the agent takes on itself.
- MCP sessions that are not fitted cost up to 1,500 more estimated tokens of instructions while the user keeps items.
- Imprint has 21 tools.
- The list is read when the server starts. An item made during a session shows up in `skills(action="list")` at once and in the handshake from the next session.
- The client names are what their MCP clients send, not observed in this installation, which recorded only `claude-code`, `claude-ai` and `local-agent-mode-imprint`. A wrong or changed name means that client gets the list it already has; it never loses one it lacks.
- An agent can change the user's items on every device, with the client's approval of `skill_edit` (annotated destructive). The replaced version stays in the device's history, where the dashboard restores it.

## Invalidation conditions

- If MCP clients come to load skills from servers natively (a standard way for a server to offer skills that the client lists and loads by description), offer the items that way and drop the handshake block.
- If a client's handshake name changes, or another client that reads the folders Imprint writes is identified by its name, change the list of names.
- If users keep more items than the block shows and agents miss ones listed only by name, measure what a larger budget costs per session and change the budget.
- If a client starts loading skills from WSL profiles' folders that a Windows installation writes, fit the block for bridged sessions as for local ones.

## Validation

- `tests/test_skills_tool.py` (7 tests), run on Windows 11 with the Python 3.13 runtime:
  - Nothing is listed, and no registry is made, without items. Active items are listed and disabled ones are not.
  - Claude Code with both folders written gets no block. With only its skills folder written it gets the personas. Served to WSL it gets everything.
  - The block stays within its budget, with long descriptions cut and the rest counted.
  - `list` and `get` work, including paging a 30,000-character file and refusing an ambiguous name. `create` and `edit` write into the folders, a credential is refused, and the change is kept when the clients cannot be listed.
  - Through FastMCP's in-memory client, on both the `initialize` handshake and `server/discover`: `claude-ai` gets the block and `claude-code` does not, `skills` is annotated read-only and `skill_edit` destructive, and a skill created over MCP lands in the folder.
- `tests/test_recall.py` counts 21 tools. `tests/test_chat_tool_coverage.py` checks that the chat offers `skills` and lists `skill_edit` among the tools it does not offer.
- `tests/test_voice.py` checks that Echo offers `skills` with its read tools.
- The full Python suite on the same machine ran 1,207 tests (18 skipped). Its only failure was the voice test's list of offered tools, which was then updated for `skills`; that module's 66 tests were rerun and pass.
- Not verified: a real Claude Desktop, claude.ai, Codex or Cursor session receiving the block and loading an item through the tool, and the handshake names Codex and Cursor send.
