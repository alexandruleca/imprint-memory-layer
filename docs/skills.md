---
title: Skills and personas
description: How Imprint keeps your skills and personas once, writes them into the folders your coding clients read, takes back edits made there, and syncs them to your paired devices.
---

A **skill** is instructions a coding agent loads when its description matches the task: a folder with a `SKILL.md` (YAML front matter with `name` and `description`, then the instructions) and, optionally, scripts and reference files. A **persona** is a role Claude Code can hand work to as a subagent: one Markdown file with the same front matter followed by the role's prompt.

Imprint keeps each one once, in a registry outside every workspace. It writes them into the folders your connected clients read, takes back edits you make in those folders, and keeps them the same on every paired device. [ADR 0168](adr/0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) records the registry, and [ADR 0169](adr/0169-skills-and-personas-are-written-into-the-folders-clients-read.md) records how items reach the clients.

## Where they go

Each client reads skills from its own folders. Imprint writes each skill into the fewest of them that reach every connected client:

| Client | Reads skills from | Personas |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` | `~/.claude/agents/<name>.md` |
| Codex | `~/.agents/skills` | — |
| Cursor | `~/.agents/skills`, `~/.cursor/skills`, `~/.claude/skills`, `~/.codex/skills` | — |
| GitHub Copilot | `~/.agents/skills`, `~/.copilot/skills` | — |
| OpenCode | `~/.agents/skills`, `~/.config/opencode/skills`, `~/.claude/skills` | — |

With Claude Code and Codex connected, skills go to `~/.claude/skills` and `~/.agents/skills`; Cursor, Copilot and OpenCode read one of those already. Cline, Claude Desktop, ChatGPT Desktop and OpenClaw get none. The Skills page shows the folders in use and which clients read each.

Only this computer's folders are written: WSL profiles connected to a Windows installation get no skills yet.

## What Imprint touches

Each folder Imprint writes into holds `.imprint-managed.json`, listing what Imprint wrote there. From it:

- **What you made yourself stays yours.** A skill folder or persona file Imprint did not write is never overwritten or removed. When one has the name of an item in the registry, the Skills page reports it; adopt it or rename one of the two.
- **Edits in a client come back.** Change a skill in `~/.claude/skills` by hand or with a client's own skill tools, and Imprint takes the change into the registry on its next pass and writes it to the other folders and devices. When the registry also changed in the meantime, for example from another device, the newer change wins and the other is kept in this device's history. An edit that is not valid yet (broken front matter, a credential) is left as it is and reported until it is.
- **Removing happens in Imprint.** Removing or disabling an item takes it out of every folder, on every paired device, as long as the folder still holds what Imprint wrote. A skill you delete in a client's folder is written again.

Passes run after every change in Imprint, after a sync brings items, when the dashboard starts and every 5 minutes while it runs, after `imprint connect` and `imprint disconnect`, and on `imprint skills apply`.

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
