---
title: ADR 0169 — Skills and personas are written into the folders the clients read
description: Imprint writes each active skill into the fewest of the connected clients' own skill folders that reach every one of them, and each persona into Claude Code's subagents folder, records what it wrote in a manifest there, takes back edits made in those folders, and never touches what it did not write.
---

- Status: Accepted
- Date: 2026-11-09
- Related: [ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md) (the registry), [ADR 0053](0053-connect-clients-without-touching-the-runtime.md) (the client table), [ADR 0006](0006-durable-mcp-and-preserved-client-config.md) (preserved client configuration), [skills](../skills.md)

## Context

The registry ([ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md)) is useful only if the clients load its items. Where each client reads user-level skills, from its documentation on 2026-11-09:

| Client | Folders | Source |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` (its config folder) | code.claude.com/docs/en/skills |
| Codex | `$HOME/.agents/skills` | developers.openai.com/codex/skills |
| Cursor | `~/.agents/skills`, `~/.cursor/skills`, and for compatibility `~/.claude/skills` and `~/.codex/skills` | cursor.com/docs/skills |
| GitHub Copilot | `~/.copilot/skills` or `~/.agents/skills` | docs.github.com, Copilot "about agent skills" |
| OpenCode | `~/.config/opencode/skills`, and for compatibility `~/.claude/skills` and `~/.agents/skills` | opencode.ai/docs/skills |

- Claude Code reads subagents from `~/.claude/agents/<name>.md`. No other connected client's subagent format was checked.
- Codex: "If two skills share the same `name`, Codex doesn't merge them; both can appear in skill selectors." Cursor and OpenCode read several of these folders. Writing a skill into every client's own folder shows it more than once.
- The user may already keep skills in these folders, and clients write there themselves (Claude Code and Codex ship skills that create skills).
- The Go client table ([ADR 0053](0053-connect-clients-without-touching-the-runtime.md)) is the one place that knows each client and whether this runtime is connected to it. The dashboard reads it through `imprint clients --json`.

## Decision

**Which folders.** The client table lists, for each client, the folders it reads skills from (preferred first) and the folder it reads personas from: Claude Code's are its own; Codex, Cursor, Copilot and OpenCode list `~/.agents/skills` first. `imprint clients --json` reports them. For the connected clients, skills go into the fewest folders that reach every one of them: the clients that read the fewest folders choose first, and a client that already reads a chosen folder adds none. With Claude Code and Codex connected, that is `~/.claude/skills` and `~/.agents/skills`. Personas go to Claude Code's `~/.claude/agents` as `<name>.md`.

**A manifest says what Imprint wrote.** Each folder Imprint writes into holds `.imprint-managed.json`: this runtime's data folder, and each item written there, by name, with the content hash of what was written. On each pass:

1. An item whose files differ from what the manifest records was edited in the client. The edit is taken into the registry when it is newer than the registry's last change of the item; otherwise it goes to this device's history and the registry's version is written over it. An edit that is not a valid item (front matter, a credential) is left in place, untouched, and reported until it is valid.
2. Each active item is written where it is missing or differs. A file or folder of the same name that Imprint did not write is left alone and reported, unless it already holds exactly the item, in which case it is recorded as managed from then on.
3. An item that is removed, disabled or renamed is removed from a folder only while it still holds what Imprint wrote. So are Imprint's items in a folder that no connected client reads any more, which `data/registry-folders.json` remembers.
4. An item deleted in the client is written again: items are removed in Imprint.

A manifest written by another runtime of Imprint, and a name in a manifest that is not a valid item name, are ignored. When the clients cannot be listed, nothing is written or removed.

**When.** After every change made through the CLI or the dashboard, after a sync stores received items, when the dashboard starts and every 5 minutes while it runs, after `imprint connect` and `imprint disconnect`, and on `imprint skills apply`. A pass with no items and no folder written before does nothing.

**Found items.** Skill folders and persona files in the connected clients' folders that Imprint did not write and does not hold are listed (`imprint skills found`, the dashboard's Skills page) for the user to adopt into the registry. An adopted item stays where it was.

## Rejected alternatives

- **Every client's own folder.** Simplest, but Cursor would see a skill up to four times and OpenCode three times, and Codex shows both copies of a duplicate name.
- **Only `~/.agents/skills`.** Claude Code's documentation lists only `~/.claude/skills` for personal skills.
- **MCP prompts from Imprint's server instead of files.** Every MCP client would get them without files, but a prompt runs only when the user invokes it. A client loads a skill on its own when the description matches the task, and only from its folders.
- **Symbolic links from one folder into the others.** Creating symbolic links on Windows requires Developer Mode or administrator rights, and of the clients only Codex documents following them.
- **The manifest in Imprint's data folder only.** A reinstalled runtime or a second installation could not tell which folders in a client Imprint wrote, and would treat them as the user's or overwrite them.
- **Treating a skill deleted in a client as removed.** Clients and tools that rewrite their folders could then remove an item on every paired device ([ADR 0168](0168-skills-and-personas-are-kept-once-and-synced-to-paired-devices.md)). Removal stays an explicit act in Imprint.

## Consequences

- One write reaches every connected client. A client that reads a chosen folder and its own sees no extra copy, except where two clients' only folders differ: with Claude Code and Codex both connected, Cursor, Copilot and OpenCode read both `~/.claude/skills` and `~/.agents/skills` and may list a skill twice.
- The user can keep editing skills where they did before, in any client or with a client's own skill tools; the edit reaches the registry and the other devices within one pass.
- Folders the user owns are never overwritten or removed. A name taken by the user's own skill is reported until the user adopts it or renames one of them.
- Personas reach only Claude Code. Subagent files of other clients were not checked.
- Only this computer's folders are written. A WSL profile connected to a Windows runtime ([ADR 0026](0026-windows-installer-wsl-client-bridge.md)) gets no skills.
- Edits made in a client's folder wait for the next pass while the dashboard does not run.

## Invalidation conditions

- If Claude Code reads `~/.agents/skills`, write skills only there, and remove the copies in `~/.claude/skills`.
- If a client stops reading a listed folder or starts reading another, change the client table.
- If WSL clients connected to a Windows runtime need skills, write the recorded WSL profiles' folders while their distribution runs, without starting a stopped one.
- If users expect a skill deleted in a client to stay deleted, treat a deleted managed item as disabled on that device.
- If other clients' subagent formats are confirmed, write personas for them too.

## Validation

- `tests/test_registry_files.py`, run on Windows 11 with the Python 3.13 runtime: the fewest folders for several sets of connected clients; items written byte for byte with a manifest, and a second pass that writes nothing; a folder Imprint did not write left alone, found and adopted; an identical folder recorded as managed; an edit in a client taken back and written to the other folder; an invalid edit left in place until fixed; a newer change from another device winning over an older edit, which stays in history; a rename moving the folder; removed items and folders no client reads cleaned while the user's own folder there stays; nothing written or removed when the clients cannot be listed; another installation's manifest respected; a manifest name pointing outside its folder ignored; the dashboard's API creating, editing, restoring, removing and adopting.
- `cmd/clients_test.go` (Go 1.26 on Linux; `go vet` for Windows and macOS): the client table lists the documented folders, `~/.agents/skills` first for the clients that read it, and only Claude Code has a personas folder.
- `imprint/ui/tests/skills.spec.ts` (Playwright, Chromium on Linux) against a mocked API: the page lists items, folders and problems; edits and saves a file; disables; writes a new skill; adopts a found one.
- Not verified: each real client loading a written skill, and the duplicate listing in Cursor, Copilot and OpenCode when both folders are written.
