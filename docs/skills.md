---
title: Skills and personas
description: How Imprint keeps your skills and personas once, writes them into the folders your coding clients read, offers them to every MCP client, takes back edits made there, and syncs them to your paired devices.
---

A **skill** is instructions a coding agent loads when its description matches the task: a folder with a `SKILL.md` (YAML front matter with `name` and `description`, then the instructions) and, optionally, scripts and reference files. A **persona** is a role Claude Code can hand work to as a subagent: one Markdown file with the same front matter followed by the role's prompt.

Imprint keeps each one once, in a registry outside every workspace. It writes them into the folders your connected clients read, offers them to every client through its MCP server, takes back edits you make in those folders, and keeps them the same on every paired device. [ADR 0168](adr/0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) records the registry. [ADR 0169](adr/0169-skills-and-personas-are-written-into-the-folders-clients-read.md) records how items reach the clients' folders, [ADR 0171](adr/0171-personas-are-written-for-codex-and-cursor.md) the persona folders of Codex and Cursor, and [ADR 0170](adr/0170-skills-and-personas-reach-every-mcp-client-through-imprints-server.md) the MCP route.

## Where they go

Each client reads skills and personas from its own folders. Imprint writes each item into the fewest of them that reach every connected client:

| Client | Reads skills from | Reads personas from |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` | `~/.claude/agents/<name>.md` |
| Codex | `~/.agents/skills` | `~/.codex/agents/<name>.toml` (under `CODEX_HOME` when set) |
| Cursor | `~/.agents/skills`, `~/.cursor/skills`, `~/.claude/skills`, `~/.codex/skills` | `~/.cursor/agents/<name>.md`, `~/.claude/agents/<name>.md` |
| GitHub Copilot | `~/.agents/skills`, `~/.copilot/skills` | — |
| OpenCode | `~/.agents/skills`, `~/.config/opencode/skills`, `~/.claude/skills` | — |

With Claude Code and Codex connected, skills go to `~/.claude/skills` and `~/.agents/skills`, and personas to `~/.claude/agents` and `~/.codex/agents`; Cursor, Copilot and OpenCode already read one of those skill folders, and Cursor reads Claude Code's agents. Cline, Claude Desktop, ChatGPT Desktop and OpenClaw read no such folders: they reach your items [through Imprint's MCP server](#through-imprints-mcp-server). The Skills page shows the folders in use and which clients read each.

Only this computer's folders are written: WSL profiles connected to a Windows installation get no files yet, and their sessions reach your items through the MCP server.

### Personas in Codex

Codex reads a custom agent as a TOML file, so a persona sits in `~/.codex/agents` as `<name>.toml` with its `name`, `description` and, as `developer_instructions`, the text after its front matter. Settings for Codex alone go in the persona's front matter under `codex:` and are written as top-level keys; Claude Code and Cursor ignore them:

```markdown
---
name: reviewer
description: Reviews diffs for bugs before they merge
tools: Read, Grep
codex:
  model_reasoning_effort: high
---

You review code...
```

Only text, numbers and true/false values carry over. An edit made in the TOML file comes back into the persona like any other edit: its name, description, instructions and settings. The rest of the persona's front matter stays as it was. A file that sets a table, such as `[mcp_servers]`, is left as it is and reported. Codex spawns a custom agent when you ask for it; Cursor and Claude Code also delegate to a persona on their own when its description fits.

## What Imprint touches

Each folder Imprint writes into holds `.imprint-managed.json`, listing what Imprint wrote there. From it:

- **What you made yourself stays yours.** A skill folder or persona file Imprint did not write is never overwritten or removed. When one has the name of an item in the registry, the Skills page reports it; adopt it or rename one of the two.
- **Edits in a client come back.** Change a skill in `~/.claude/skills` by hand or with a client's own skill tools, and Imprint takes the change into the registry on its next pass and writes it to the other folders and devices. When the registry also changed in the meantime, for example from another device, the newer change wins and the other is kept in this device's history. An edit that is not valid yet (broken front matter, a credential) is left as it is and reported until it is.
- **Removing happens in Imprint.** Removing or disabling an item takes it out of every folder, on every paired device, as long as the folder still holds what Imprint wrote. A skill you delete in a client's folder is written again.

Passes run after every change in Imprint, after a sync brings items, when the dashboard starts and every 5 minutes while it runs, after `imprint connect` and `imprint disconnect`, and on `imprint skills apply`.

## Through Imprint's MCP server

Every client connected to Imprint, the desktop apps and WSL sessions included, can find and use your items without any folder:

- **Listed at the start of each session.** The MCP server's instructions name each active skill and persona with its description, after your guidelines, within about 1,500 tokens. A client that already loads them from its own folders on this computer (Claude Code, Codex, Cursor or OpenCode, as the last write reported) is not given that list again.
- **`skills`** (read-only) lists them with `action="list"` and reads one with `action="get", name=...`: its `SKILL.md` or `PERSONA.md`, or another file of a skill with `path`, paged with `start`.
- **`skill_edit`** creates one (`action="create"`, from a name, description and instructions, or from its files) or changes one (`action="edit"`: a new description or instructions, files added, replaced or removed with `null`, or `state` `disabled` or `active`). Each change is written into the clients' folders at once and reaches your paired devices with the next sync; the replaced version stays in this device's history. Removing an item is yours to do, here or on the Skills page.

## Rules for an item

- `name`: lower-case letters, digits and hyphens, at most 64 characters. It names the skill's folder or the persona's file.
- `description`: 1 to 1,024 characters. Agents read it to decide when to use the item, so say what it does and when.
- Text files only, at most 256 KiB each and 1 MiB and 64 files in all. Files under `__pycache__`, `.git` or `node_modules` and system files such as `.DS_Store` are not part of a skill.
- No credentials. A file holding one is refused, naming the file and the kind of secret, instead of being stored redacted: a redacted script would no longer run.

## Sync

Items travel to paired devices as their own dataset, like core memories; a device whose Imprint predates skills neither sends nor receives them. The newest change of each item wins; the version it replaced stays in that device's history (the last 10 versions, never synced), where the Skills page can restore it. If two devices made an item with the same name before they synced, every device gives the clients the older one and marks the other.

## Commands

```bash
imprint skills                  # list skills and personas
imprint skills show NAME        # print an item's files
imprint skills add PATH         # keep a skill folder (with SKILL.md) or, with --persona, a persona file
imprint skills found            # skills and personas in the clients' folders that Imprint does not keep
imprint skills adopt PATH|NAME  # keep one of those (--all for every valid one)
imprint skills disable NAME     # take it out of the clients, keep it
imprint skills enable NAME
imprint skills remove NAME      # remove it here and on every paired device
imprint skills apply            # write into the clients' folders now and take back edits
```

`--kind skill|persona` picks between a skill and a persona of the same name. The dashboard's **Skills** page does the same: write a new skill or persona, edit its files, see earlier versions, and adopt what your clients already have.
